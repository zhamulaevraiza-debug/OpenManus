import asyncio
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from app.config import config
from app.context import current_run
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.net_guard import UnsafeURLError, check_url_sync
from app.tool.search import (
    BaiduSearchEngine,
    BingSearchEngine,
    DuckDuckGoSearchEngine,
    GoogleSearchEngine,
    WebSearchEngine,
)
from app.tool.search.base import SearchItem


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_FETCHED_CHARS = 10000
MAX_REDIRECTS = 5
ENGINE_TIMEOUT_SECONDS = 30

# Per-engine attempts and maximum backoff (seconds) between them.
CLI_ENGINE_ATTEMPTS = 3
CLI_ENGINE_MAX_BACKOFF = 10
# Retry policy when a web run is active: users are waiting on the result.
SERVER_ENGINE_ATTEMPTS = 2
SERVER_ENGINE_MAX_BACKOFF = 2
SERVER_MAX_RETRY_DELAY = 5
SERVER_MAX_RETRIES = 1

ENGINE_FACTORIES: Dict[str, Callable[[], WebSearchEngine]] = {
    "google": GoogleSearchEngine,
    "baidu": BaiduSearchEngine,
    "duckduckgo": DuckDuckGoSearchEngine,
    "bing": BingSearchEngine,
}


class SearchResult(BaseModel):
    """Represents a single search result returned by a search engine."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    position: int = Field(description="Position in search results")
    url: str = Field(description="URL of the search result")
    title: str = Field(default="", description="Title of the search result")
    description: str = Field(
        default="", description="Description or snippet of the search result"
    )
    source: str = Field(description="The search engine that provided this result")
    raw_content: Optional[str] = Field(
        default=None, description="Raw content from the search result page if available"
    )

    def __str__(self) -> str:
        """String representation of a search result."""
        return f"{self.title} ({self.url})"


class SearchMetadata(BaseModel):
    """Metadata about the search operation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    total_results: int = Field(description="Total number of results found")
    language: str = Field(description="Language code used for the search")
    country: str = Field(description="Country code used for the search")


class SearchResponse(ToolResult):
    """Structured response from the web search tool, inheriting ToolResult."""

    query: str = Field(description="The search query that was executed")
    results: List[SearchResult] = Field(
        default_factory=list, description="List of search results"
    )
    metadata: Optional[SearchMetadata] = Field(
        default=None, description="Metadata about the search"
    )

    @model_validator(mode="after")
    def populate_output(self) -> "SearchResponse":
        """Populate output or error fields based on search results."""
        if self.error:
            return self

        result_text = [f"Search results for '{self.query}':"]

        for i, result in enumerate(self.results, 1):
            # Add title with position number
            title = result.title.strip() or "No title"
            result_text.append(f"\n{i}. {title}")

            # Add URL with proper indentation
            result_text.append(f"   URL: {result.url}")

            # Add description if available
            if result.description.strip():
                result_text.append(f"   Description: {result.description}")

            # Add content preview if available
            if result.raw_content:
                content_preview = result.raw_content[:1000].replace("\n", " ").strip()
                if len(result.raw_content) > 1000:
                    content_preview += "..."
                result_text.append(f"   Content: {content_preview}")

        # Add metadata at the bottom if available
        if self.metadata:
            result_text.extend(
                [
                    "\nMetadata:",
                    f"- Total results: {self.metadata.total_results}",
                    f"- Language: {self.metadata.language}",
                    f"- Country: {self.metadata.country}",
                ]
            )

        self.output = "\n".join(result_text)
        return self


class WebContentFetcher:
    """Utility class for fetching web content."""

    @staticmethod
    async def fetch_content(url: str, timeout: int = 10) -> Optional[str]:
        """
        Fetch and extract the main content from a webpage.

        Only public http(s) addresses are fetched (every redirect hop is checked).

        Args:
            url: The URL to fetch content from
            timeout: Request timeout in seconds

        Returns:
            Extracted text content or None if fetching fails
        """
        try:
            return await asyncio.to_thread(WebContentFetcher._fetch_text, url, timeout)
        except Exception as e:
            logger.warning(f"Error fetching content from {url}: {e}")
            return None

    @staticmethod
    def _fetch_text(url: str, timeout: int) -> Optional[str]:
        response = WebContentFetcher._get_public(url, timeout)
        if response is None:
            return None
        if response.status_code != 200:
            logger.warning(
                f"Failed to fetch content from {url}: HTTP {response.status_code}"
            )
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "header", "footer", "nav"]):
            element.extract()
        text = " ".join(soup.get_text(separator="\n", strip=True).split())
        return text[:MAX_FETCHED_CHARS] if text else None

    @staticmethod
    def _get_public(url: str, timeout: int) -> Optional[requests.Response]:
        """GET ``url`` following redirects manually; every hop must be public."""
        headers = {"User-Agent": USER_AGENT}
        with requests.Session() as session:
            for _ in range(MAX_REDIRECTS + 1):
                try:
                    check_url_sync(url)
                except UnsafeURLError as e:
                    logger.warning(f"Skipping content fetch: {e.message}")
                    return None
                response = session.get(
                    url, headers=headers, timeout=timeout, allow_redirects=False
                )
                location = response.headers.get("Location")
                if not response.is_redirect or not location:
                    return response
                url = urljoin(url, location)
        logger.warning(f"Too many redirects while fetching {url}")
        return None


class WebSearch(BaseTool):
    """Search the web for information using various search engines."""

    name: str = "web_search"
    description: str = """Search the web for real-time information about any topic.
    This tool returns comprehensive search results with relevant information, URLs, titles, and descriptions.
    If the primary search engine fails, it automatically falls back to alternative engines."""
    parameters: dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "(required) The search query to submit to the search engine.",
            },
            "num_results": {
                "type": "integer",
                "description": "(optional) The number of search results to return. Default is 5.",
                "default": 5,
            },
            "lang": {
                "type": "string",
                "description": "(optional) Language code for search results (default: en).",
                "default": "en",
            },
            "country": {
                "type": "string",
                "description": "(optional) Country code for search results (default: us).",
                "default": "us",
            },
            "fetch_content": {
                "type": "boolean",
                "description": "(optional) Whether to fetch full content from result pages. Default is false.",
                "default": False,
            },
        },
        "required": ["query"],
    }
    content_fetcher: WebContentFetcher = WebContentFetcher()

    # Engines are created on first use (Bing opens an HTTP session).
    _search_engine: Optional[Dict[str, WebSearchEngine]] = PrivateAttr(default=None)

    @property
    def search_engines(self) -> Dict[str, WebSearchEngine]:
        if self._search_engine is None:
            self._search_engine = {
                name: factory() for name, factory in ENGINE_FACTORIES.items()
            }
        return self._search_engine

    async def execute(
        self,
        query: str,
        num_results: int = 5,
        lang: Optional[str] = None,
        country: Optional[str] = None,
        fetch_content: bool = False,
    ) -> SearchResponse:
        """
        Execute a Web search and return detailed search results.

        Args:
            query: The search query to submit to the search engine
            num_results: The number of search results to return (default: 5)
            lang: Language code for search results (default from config)
            country: Country code for search results (default from config)
            fetch_content: Whether to fetch content from result pages (default: False)

        Returns:
            A structured response containing search results and metadata
        """
        # Get settings from config
        retry_delay = (
            getattr(config.search_config, "retry_delay", 60)
            if config.search_config
            else 60
        )
        max_retries = (
            getattr(config.search_config, "max_retries", 3)
            if config.search_config
            else 3
        )
        if current_run() is not None:
            retry_delay = min(retry_delay, SERVER_MAX_RETRY_DELAY)
            max_retries = min(max_retries, SERVER_MAX_RETRIES)
        num_results = max(1, min(int(num_results or 5), 20))

        # Use config values for lang and country if not specified
        if lang is None:
            lang = (
                getattr(config.search_config, "lang", "en")
                if config.search_config
                else "en"
            )

        if country is None:
            country = (
                getattr(config.search_config, "country", "us")
                if config.search_config
                else "us"
            )

        search_params = {"lang": lang, "country": country}

        # Try searching with retries when all engines fail
        for retry_count in range(max_retries + 1):
            results = await self._try_all_engines(query, num_results, search_params)

            if results:
                # Fetch content if requested
                if fetch_content:
                    results = await self._fetch_content_for_results(results)

                # Return a successful structured response
                return SearchResponse(
                    status="success",
                    query=query,
                    results=results,
                    metadata=SearchMetadata(
                        total_results=len(results),
                        language=lang,
                        country=country,
                    ),
                )

            if retry_count < max_retries:
                # All engines failed, wait and retry
                logger.warning(
                    f"All search engines failed. Waiting {retry_delay} seconds before retry {retry_count + 1}/{max_retries}..."
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    f"All search engines failed after {max_retries} retries. Giving up."
                )

        # Return an error response
        return SearchResponse(
            query=query,
            error="All search engines failed to return results after multiple retries.",
            results=[],
        )

    async def _try_all_engines(
        self, query: str, num_results: int, search_params: Dict[str, Any]
    ) -> List[SearchResult]:
        """Try all search engines in the configured order."""
        engine_order = self._get_engine_order()
        failed_engines = []

        engines = self.search_engines
        for engine_name in engine_order:
            engine = engines[engine_name]
            logger.info(f"🔎 Attempting search with {engine_name.capitalize()}...")
            try:
                search_items = await self._perform_search_with_engine(
                    engine, query, num_results, search_params
                )
            except Exception as e:
                logger.warning(f"Search with {engine_name.capitalize()} failed: {e}")
                failed_engines.append(engine_name)
                continue

            search_items = [item for item in search_items if item and item.url]
            if not search_items:
                failed_engines.append(engine_name)
                continue

            if failed_engines:
                logger.info(
                    f"Search successful with {engine_name.capitalize()} after trying: {', '.join(failed_engines)}"
                )

            # Transform search items into structured results
            return [
                SearchResult(
                    position=i + 1,
                    url=item.url,
                    title=item.title
                    or f"Result {i+1}",  # Ensure we always have a title
                    description=item.description or "",
                    source=engine_name,
                )
                for i, item in enumerate(search_items)
            ]

        if failed_engines:
            logger.error(f"All search engines failed: {', '.join(failed_engines)}")
        return []

    async def _fetch_content_for_results(
        self, results: List[SearchResult]
    ) -> List[SearchResult]:
        """Fetch and add web content to search results."""
        if not results:
            return []

        # Create tasks for each result
        tasks = [self._fetch_single_result_content(result) for result in results]

        # Type annotation to help type checker
        fetched_results = await asyncio.gather(*tasks)

        # Explicit validation of return type
        return [
            (
                result
                if isinstance(result, SearchResult)
                else SearchResult(**result.dict())
            )
            for result in fetched_results
        ]

    async def _fetch_single_result_content(self, result: SearchResult) -> SearchResult:
        """Fetch content for a single search result."""
        if result.url:
            content = await self.content_fetcher.fetch_content(result.url)
            if content:
                result.raw_content = content
        return result

    def _get_engine_order(self) -> List[str]:
        """Determines the order in which to try search engines."""
        preferred = (
            getattr(config.search_config, "engine", "google").lower()
            if config.search_config
            else "google"
        )
        fallbacks = (
            [engine.lower() for engine in config.search_config.fallback_engines]
            if config.search_config
            and hasattr(config.search_config, "fallback_engines")
            else []
        )

        # Start with preferred engine, then fallbacks, then remaining engines
        engines = self.search_engines
        engine_order = [preferred] if preferred in engines else []
        engine_order.extend(
            [fb for fb in fallbacks if fb in engines and fb not in engine_order]
        )
        engine_order.extend([e for e in engines if e not in engine_order])

        return engine_order

    async def _perform_search_with_engine(
        self,
        engine: WebSearchEngine,
        query: str,
        num_results: int,
        search_params: Dict[str, Any],
    ) -> List[SearchItem]:
        """Execute search with the given engine, retrying transient failures.

        Engines are synchronous libraries and run in worker threads; each attempt is
        bounded by ``ENGINE_TIMEOUT_SECONDS``. Web runs retry once with a short delay.
        """
        server_mode = current_run() is not None
        retrying = AsyncRetrying(
            stop=stop_after_attempt(
                SERVER_ENGINE_ATTEMPTS if server_mode else CLI_ENGINE_ATTEMPTS
            ),
            wait=wait_exponential(
                multiplier=1,
                min=1,
                max=SERVER_ENGINE_MAX_BACKOFF
                if server_mode
                else CLI_ENGINE_MAX_BACKOFF,
            ),
            reraise=True,
        )

        def search() -> List[SearchItem]:
            return list(
                engine.perform_search(
                    query,
                    num_results=num_results,
                    lang=search_params.get("lang"),
                    country=search_params.get("country"),
                )
            )

        async def attempt() -> List[SearchItem]:
            return await asyncio.wait_for(
                asyncio.to_thread(search), ENGINE_TIMEOUT_SECONDS
            )

        return await retrying(attempt)


if __name__ == "__main__":
    web_search = WebSearch()
    search_response = asyncio.run(
        web_search.execute(
            query="Python programming", fetch_content=True, num_results=1
        )
    )
    print(search_response)
