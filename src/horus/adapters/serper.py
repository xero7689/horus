"""Serper.dev Google Search adapter.

Uses Serper's Google Search API (https://google.serper.dev/search) via httpx POST.
No Playwright / browser required. Credentials are env-only (HORUS_SERPER_API_KEY).

Usage:
    horus crawl serper --query "python crawler"
    horus crawl serper --query "python crawler" --limit 30
    horus crawl serper --query "python crawler" --gl us --hl en
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

_SERPER_URL = "https://google.serper.dev/search"
_DEFAULT_GL = "tw"
_DEFAULT_HL = "zh-tw"


def _make_item_id(url: str, query: str) -> str:
    """Stable ID: SHA-1 of (url, query) truncated to 16 hex chars."""
    digest = hashlib.sha1(f"{url}|{query}".encode()).hexdigest()  # nosec B324
    return digest[:16]


class SerperAdapter(SiteAdapter):
    """Search Google via Serper.dev and store organic results as ScrapedItems."""

    site_id = "serper"
    display_name = "Serper (Google Search)"
    login_url = ""
    requires_login = False
    has_http_mode = True
    description = "Search Google via Serper.dev API (env key, no browser)"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        # transport injectable for tests (httpx.MockTransport); None = real network.
        self._transport = transport

    # --- SiteAdapter abstract methods (unused in http mode) ---

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        return []

    def get_urls(self, **kwargs: Any) -> list[str]:
        return [_SERPER_URL]

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {"name": "--query", "help": "Search query", "required": True, "default": None},
            {
                "name": "--gl",
                "help": "Country (geo-location), default tw",
                "required": False,
                "default": _DEFAULT_GL,
            },
            {
                "name": "--hl",
                "help": "UI language, default zh-tw",
                "required": False,
                "default": _DEFAULT_HL,
            },
        ]

    # --- parsing (pure, testable) ---

    def parse_response_json(
        self, body: dict[str, Any], *, query: str, gl: str, hl: str
    ) -> list[ScrapedItem]:
        now = datetime.now(UTC)
        items: list[ScrapedItem] = []
        for idx, org in enumerate(body.get("organic", []), start=1):
            url = (org.get("link") or "").strip()
            title = (org.get("title") or "").strip()
            if not url or not title:
                continue
            items.append(
                ScrapedItem(
                    id=_make_item_id(url, query),
                    site_id="serper",
                    url=url,
                    text=title,
                    timestamp=now,
                    extra={
                        "query": query,
                        "rank": org.get("position", idx),
                        "snippet": org.get("snippet"),
                        "date": org.get("date"),
                        "gl": gl,
                        "hl": hl,
                    },
                )
            )
        return items
