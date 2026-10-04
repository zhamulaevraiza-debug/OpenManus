"""Crawl4aiTool: SSRF checks, truncation and cache policy."""

import pytest

from app.tool import crawl4ai
from app.tool.crawl4ai import Crawl4aiTool


pytestmark = pytest.mark.asyncio


async def test_private_and_non_http_urls_are_rejected(
    block_private_network, local_site
):
    local_site.html("/", "<p>internal</p>")
    result = await Crawl4aiTool().execute(
        urls=[local_site.url("/"), "file:///etc/passwd"]
    )
    assert result.error.startswith("No valid URLs provided")
    assert "private or reserved address" in result.error
    assert "only http and https" in result.error
    assert local_site.requests == []


async def test_content_is_truncated(
    allow_private_network, run_ctx, local_site, monkeypatch
):
    monkeypatch.setattr(crawl4ai, "MAX_TOTAL_CONTENT_CHARS", 2000)
    monkeypatch.setattr(crawl4ai, "MIN_CONTENT_CHARS_PER_URL", 500)
    local_site.html(
        "/",
        "<html><head><title>Big</title></head><body><p>"
        + "word " * 5000
        + "</p></body></html>",
    )
    result = await Crawl4aiTool().execute(urls=[local_site.url("/"), "file:///x"])
    assert result.error is None, result.error
    assert "📄 Title: Big" in result.output
    assert "[truncated" in result.output
    assert "Failed: 1" in result.output and "Total URLs: 2" in result.output
    assert len(result.output) < 4000


async def test_crawler_browser_blocks_private_subresources(
    block_private_network, run_ctx, local_site, monkeypatch
):
    import crawl4ai as crawl4ai_package

    from app.tool import net_guard

    local_site.html(
        "/",
        "<html><head><title>Pub</title></head><body><p>public words</p>"
        f"<img src='http://127.0.0.1:{local_site.port}/private.png'></body></html>",
    )
    real_resolve = net_guard._resolve

    async def fake_resolve(host, port):
        if host == "public.test":
            return ["93.184.216.34"]
        return await real_resolve(host, port)

    original_config = crawl4ai_package.BrowserConfig

    def mapped_config(**kwargs):
        # The crawler's browser reaches the local server under a "public" name.
        return original_config(
            **kwargs, extra_args=["--host-resolver-rules=MAP public.test 127.0.0.1"]
        )

    monkeypatch.setattr(net_guard, "_resolve", fake_resolve)
    monkeypatch.setattr(crawl4ai_package, "BrowserConfig", mapped_config)

    result = await Crawl4aiTool().execute(
        urls=[f"http://public.test:{local_site.port}/"]
    )

    assert result.error is None, result.error
    assert "📄 Title: Pub" in result.output
    assert [path for path, _ in local_site.requests] == ["/"]
