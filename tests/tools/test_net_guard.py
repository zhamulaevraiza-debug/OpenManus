"""SSRF guard: URL/IP classification, DNS checks and the Playwright integration."""

import asyncio

import pytest
import pytest_asyncio

from app.tool import net_guard
from app.tool.net_guard import (
    UnsafeURLError,
    check_url,
    check_url_sync,
    enforce_browser_policy,
    guard_browser_context,
    is_public_ip,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.10.0.5",
        "10.0.0.1",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",  # AWS IPv6 metadata
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "2002:7f00:1::",  # 6to4 of 127.0.0.1
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "ff02::1",
        "not-an-ip",
    ],
)
def test_non_public_addresses(address):
    assert not is_public_ip(address)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "64:ff9b::808:808"]
)
def test_public_addresses(address):
    assert is_public_ip(address)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "chrome://settings",
        "data:text/html,<b>x</b>",
        "javascript:alert(1)",
        "ftp://example.com/x",
        "about:blank",
        "http://",
        "http://example.com:99999/",
    ],
)
@pytest.mark.asyncio
async def test_rejects_bad_schemes_and_malformed_urls(block_private_network, url):
    with pytest.raises(UnsafeURLError):
        await check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/api",
        "http://foo.localhost/",
        "http://[::1]/",
        "http://0x7f.1/",
        "http://2130706433/",
        "http://127.1/",
        "http://0.0.0.0:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "https://10.1.2.3/",
        "http://user:pw@192.168.0.1/",
    ],
)
@pytest.mark.asyncio
async def test_blocks_private_hosts(block_private_network, url):
    with pytest.raises(UnsafeURLError):
        await check_url(url)
    with pytest.raises(UnsafeURLError):
        check_url_sync(url)


@pytest.mark.asyncio
async def test_hostnames_are_resolved(block_private_network, monkeypatch):
    async def fake_resolve(host, port):
        return {"evil.example": ["10.0.0.5"], "good.example": ["93.184.216.34"]}[host]

    monkeypatch.setattr(net_guard, "_resolve", fake_resolve)
    with pytest.raises(UnsafeURLError, match="10.0.0.5"):
        await check_url("https://evil.example/path")
    assert await check_url("https://good.example/path") == "https://good.example/path"


@pytest.mark.asyncio
async def test_any_private_record_blocks(block_private_network, monkeypatch):
    async def fake_resolve(host, port):
        return ["93.184.216.34", "127.0.0.1"]

    monkeypatch.setattr(net_guard, "_resolve", fake_resolve)
    with pytest.raises(UnsafeURLError):
        await check_url("https://mixed.example/")


@pytest.mark.asyncio
async def test_unresolvable_hosts_are_left_to_the_fetch(block_private_network):
    assert await check_url("https://does-not-exist.invalid/")


@pytest.mark.asyncio
async def test_env_allows_private_network_but_not_other_schemes(
    allow_private_network,
):
    assert await check_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000/"
    with pytest.raises(UnsafeURLError):
        await check_url("file:///etc/passwd")


# --- Playwright integration -------------------------------------------------


@pytest_asyncio.fixture
async def chromium():
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            # "public.test" is mapped to the local test server by the browser only.
            args=["--host-resolver-rules=MAP public.test 127.0.0.1"],
        )
        try:
            yield browser
        finally:
            await browser.close()


@pytest.fixture
def public_test_host(monkeypatch):
    """Our guard resolves public.test to a public address (the browser uses 127.0.0.1)."""
    real_resolve = net_guard._resolve

    async def fake_resolve(host, port):
        if host == "public.test":
            return ["93.184.216.34"]
        return await real_resolve(host, port)

    monkeypatch.setattr(net_guard, "_resolve", fake_resolve)


@pytest.mark.asyncio
async def test_browser_guard_blocks_private_and_file_navigation(
    block_private_network, chromium, local_site
):
    local_site.html("/", "<h1>local</h1>")
    context = await chromium.new_context()
    await guard_browser_context(context)
    await guard_browser_context(context)  # idempotent
    page = await context.new_page()

    with pytest.raises(Exception):
        await page.goto(local_site.url("/"))
    with pytest.raises(Exception):
        await page.goto("file:///etc/hostname")
    assert local_site.requests == []
    await context.close()


@pytest.mark.asyncio
async def test_browser_guard_blocks_private_subresources(
    block_private_network, chromium, local_site, public_test_host
):
    local_site.html(
        "/page",
        f"<h1>public page</h1><img src='http://127.0.0.1:{local_site.port}/img'>",
    )
    context = await chromium.new_context()
    await guard_browser_context(context)
    page = await context.new_page()
    await page.goto(f"http://public.test:{local_site.port}/page")
    await page.wait_for_load_state("load")
    assert "public page" in await page.content()
    assert [path for path, _ in local_site.requests] == ["/page"]
    await context.close()


@pytest.mark.asyncio
async def test_browser_redirect_to_private_address_is_reset(
    block_private_network, chromium, local_site, public_test_host
):
    local_site.redirect("/hop", f"http://127.0.0.1:{local_site.port}/secret")
    local_site.html("/secret", "<h1>internal secret</h1>")
    context = await chromium.new_context()
    await guard_browser_context(context)
    page = await context.new_page()
    try:
        await page.goto(f"http://public.test:{local_site.port}/hop")
    except Exception:
        pass  # the navigation may be cut short by the reset

    violations = await enforce_browser_policy(context)

    assert any("/secret" in url for url in violations)
    assert page.url == "about:blank"
    assert "internal secret" not in await page.content()
    await context.close()


@pytest.mark.asyncio
async def test_enforce_policy_is_quiet_for_allowed_pages(
    allow_private_network, chromium, local_site
):
    local_site.html("/", "<h1>fine</h1>")
    context = await chromium.new_context()
    await guard_browser_context(context)
    page = await context.new_page()
    await page.goto(local_site.url("/"))
    assert await enforce_browser_policy(context) == []
    assert page.url == local_site.url("/")
    await asyncio.sleep(0)
    await context.close()
