"""DuckDuckGo search adapter.

Uses html.duckduckgo.com (pure HTML version) via direct HTTP POST.
No Playwright / browser required.

Usage:
    horus crawl ddg --query "python crawler"
    horus crawl ddg --query "python crawler" --limit 10
"""

import hashlib
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _make_item_id(url: str, query: str) -> str:
    """Stable ID: SHA-1 of (url, query) truncated to 16 hex chars."""
    digest = hashlib.sha1(f"{url}|{query}".encode()).hexdigest()
    return digest[:16]


class DuckDuckGoAdapter(SiteAdapter):
    """Search DuckDuckGo and store results as ScrapedItems.

    Each search result becomes one item:
    - text      : result title
    - url       : result URL
    - extra     : {"query": str, "rank": int, "snippet": str | None}
    - timestamp : time of the crawl (UTC now)
    """

    site_id = "ddg"
    display_name = "DuckDuckGo Search"
    login_url = ""
    requires_login = False
    has_http_mode = True
    description = "Search DuckDuckGo and store results (no browser needed)"

    # --- SiteAdapter abstract methods (unused in http mode) ---

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        return []

    def get_urls(self, **kwargs: Any) -> list[str]:
        query: str | None = kwargs.get("query")
        if not query:
            raise ValueError("ddg adapter requires --query")
        return [_DDG_HTML_URL]

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "--query",
                "help": "Search query",
                "required": True,
                "default": None,
            },
        ]

    # --- HTTP mode ---

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        """POST search query to DDG HTML endpoint, parse results."""
        query: str | None = kwargs.get("query")
        if not query:
            raise ValueError("ddg adapter requires --query")

        html = self._fetch_html(query)
        return self.parse_html(html, query=query)

    def _fetch_html(self, query: str) -> str:
        data = urllib.parse.urlencode({"q": query, "b": "", "kl": ""}).encode()
        req = urllib.request.Request(_DDG_HTML_URL, data=data, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return resp.read().decode("utf-8")  # type: ignore[no-any-return]

    def parse_html(self, html: str, *, query: str) -> list[ScrapedItem]:
        """Parse DDG HTML result page into ScrapedItems. Public for testing."""
        soup = BeautifulSoup(html, "html.parser")
        now = datetime.now(UTC)
        items: list[ScrapedItem] = []

        for rank, div in enumerate(soup.select(".result:not(.result--sep)"), start=1):
            title_elem = div.select_one(".result__title a.result__a")
            if not title_elem:
                continue

            href = title_elem.get("href", "")
            url = str(href).strip() if href else ""
            title = title_elem.get_text(strip=True)
            if not url or not title:
                continue

            snippet_elem = div.select_one(".result__snippet")
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else None

            items.append(
                ScrapedItem(
                    id=_make_item_id(url, query),
                    site_id="ddg",
                    url=url,
                    text=title,
                    timestamp=now,
                    extra={
                        "query": query,
                        "rank": rank,
                        "snippet": snippet,
                    },
                )
            )

        return items
