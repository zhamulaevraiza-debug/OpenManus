"""WebSearch engine fallthrough, retry policy and the content fetcher."""

import time
from typing import List

import pytest

from app.tool import web_search
from app.tool.net_guard import UnsafeURLError
from app.tool.search import bing_search, google_search
from app.tool.search.base import SearchItem, WebSearchEngine
from app.tool.web_search import USER_AGENT, WebContentFetcher, WebSearch


class RaisingEngine(WebSearchEngine):
    calls: int = 0

    def perform_search(self, query, num_results=10, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("HTTP 429 Too Many Requests")


class EmptyEngine(WebSearchEngine):
    def perform_search(self, query, num_results=10, *args, **kwargs):
        return []


class StaticEngine(WebSearchEngine):
    def perform_search(
        self, query, num_results=10, *args, **kwargs
    ) -> List[SearchItem]:
        return [
            SearchItem(title=f"{query} {i}", url=f"https://example.com/{i}")
            for i in range(num_results)
        ]


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(web_search, "SERVER_ENGINE_ATTEMPTS", 1)
    monkeypatch.setattr(web_search, "SERVER_MAX_RETRY_DELAY", 0)


def make_search(engines) -> WebSearch:
    tool = WebSearch()
    tool._search_engine = engines
    return tool


@pytest.mark.asyncio
async def test_failing_engines_fall_through(run_ctx, fast_retries):
    failing = RaisingEngine()
    tool = make_search(
        {"google": failing, "duckduckgo": EmptyEngine(), "bing": StaticEngine()}
    )
    response = await tool.execute(query="openmanus", num_results=2)
    assert response.error is None
    assert [r.source for r in response.results] == ["bing", "bing"]
    assert failing.calls == 1
    assert "openmanus 0" in response.output


@pytest.mark.asyncio
async def test_all_engines_failing_returns_error_quickly(run_ctx, fast_retries):
    tool = make_search({"google": RaisingEngine(), "bing": EmptyEngine()})
    started = time.monotonic()
    response = await tool.execute(query="nothing")
    assert time.monotonic() - started < 2
    assert response.error
    assert response.results == []


@pytest.mark.asyncio
async def test_server_mode_caps_retry_settings(run_ctx, monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(web_search, "SERVER_ENGINE_ATTEMPTS", 1)
    monkeypatch.setattr(web_search.asyncio, "sleep", fake_sleep)
    tool = make_search({"google": EmptyEngine()})
    await tool.execute(query="x")
    assert sleeps == [web_search.SERVER_MAX_RETRY_DELAY] * web_search.SERVER_MAX_RETRIES


@pytest.mark.asyncio
async def test_engines_are_created_lazily_per_instance():
    first, second = WebSearch(), WebSearch()
    assert first._search_engine is None
    assert first.search_engines is not second.search_engines
    assert set(first.search_engines) == {"google", "baidu", "duckduckgo", "bing"}


def test_google_plain_url_results_become_search_items(monkeypatch):
    monkeypatch.setattr(
        google_search,
        "search",
        lambda *a, **k: ["https://a.example", "https://b.example"],
    )
    items = google_search.GoogleSearchEngine().perform_search("q", num_results=2)
    assert all(isinstance(item, SearchItem) for item in items)
    assert [item.url for item in items] == ["https://a.example", "https://b.example"]


def test_bing_quotes_query_and_sets_timeout(monkeypatch):
    seen = {}

    class Response:
        text = "<html><body></body></html>"
        encoding = "utf-8"

    def fake_get(url, timeout=None):
        seen["url"], seen["timeout"] = url, timeout
        return Response()

    engine = bing_search.BingSearchEngine()
    monkeypatch.setattr(engine.session, "get", fake_get)
    engine.perform_search("a&b #c", num_results=1)
    assert seen["url"].endswith("q=a%26b+%23c")
    assert seen["timeout"] == bing_search.REQUEST_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_fetcher_sends_user_agent(allow_private_network, local_site):
    local_site.html(
        "/", "<html><body><p>Fetched text</p><script>x()</script></body></html>"
    )
    text = await WebContentFetcher.fetch_content(local_site.url("/"))
    assert text == "Fetched text"
    assert local_site.requests[0][1]["User-Agent"] == USER_AGENT


@pytest.mark.asyncio
async def test_fetcher_blocks_private_addresses(block_private_network, local_site):
    local_site.html("/", "<p>secret</p>")
    assert await WebContentFetcher.fetch_content(local_site.url("/")) is None
    assert local_site.requests == []


@pytest.mark.asyncio
async def test_fetcher_checks_every_redirect_hop(
    allow_private_network, local_site, monkeypatch
):
    local_site.redirect("/start", "/secret")
    local_site.html("/secret", "<p>secret</p>")

    def guard(url):
        if url.endswith("/secret"):
            raise UnsafeURLError(f"Blocked URL '{url}'")
        return url

    monkeypatch.setattr(web_search, "check_url_sync", guard)
    assert await WebContentFetcher.fetch_content(local_site.url("/start")) is None
    assert [path for path, _ in local_site.requests] == ["/start"]
