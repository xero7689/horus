"""Serper.dev Google Search adapter.

Uses Serper's Google Search API (https://google.serper.dev/search) via httpx POST.
No Playwright / browser required. Credentials are env-only (HORUS_SERPER_API_KEY).

Usage:
    horus crawl serper --query "python crawler"
    horus crawl serper --query "python crawler" --limit 30
    horus crawl serper --query "python crawler" --gl us --hl en
"""

import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

from horus.adapters._io import read_stdin_lines, stable_item_id
from horus.adapters.base import SiteAdapter
from horus.config import Settings, env_files
from horus.models import ScrapedItem

_SERPER_URL = "https://google.serper.dev/search"
_DEFAULT_GL = "tw"
_DEFAULT_HL = "zh-tw"


class SerperAdapter(SiteAdapter):
    """Search Google via Serper.dev and store organic results as ScrapedItems."""

    site_id = "serper"
    display_name = "Serper (Google Search)"
    login_url = ""
    requires_login = False
    has_http_mode = True
    description = "Search Google via Serper.dev API (env key, no browser)"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # transport injectable for tests (httpx.MockTransport); None = real network.
        # AsyncBaseTransport is the async-client transport type; httpx.MockTransport
        # subclasses both AsyncBaseTransport and BaseTransport, so tests still work.
        self._transport = transport

    def get_urls(self, **kwargs: Any) -> list[str]:
        # get_urls is unused in http mode; queries resolved in fetch_items
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
                    id=stable_item_id(url, query),
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

    # --- HTTP mode ---

    def _read_queries(self, **kwargs: Any) -> list[str]:
        """Resolve queries from --query kwarg or stdin."""
        query: str | None = kwargs.get("query")
        if query:
            return [query]
        lines = read_stdin_lines()
        if lines:
            return lines
        raise ValueError("serper adapter requires --query")

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        queries = self._read_queries(**kwargs)
        # Limit semantics: absent → 10 (1 credit); 0 → 100 ("unlimited", Serper hard cap);
        # otherwise clamp to 100. NOTE: via CLI the default is 50 (cli.py:119), so a bare
        # `crawl serper --query X` requests num=50 → costs 2 credits. Pass --limit 10 for 1 credit.
        raw = kwargs.get("limit")
        if raw is None:
            requested = 10
        elif int(raw) == 0:
            requested = 100
        else:
            requested = min(int(raw), 100)
        gl = kwargs.get("gl") or _DEFAULT_GL
        hl = kwargs.get("hl") or _DEFAULT_HL
        num = min(max(requested, 10), 100)  # Serper per-request cap
        if num > 10:
            print(
                f"[serper] num={num} > 10 → this query costs 2 credits (use --limit 10 for 1).",
                file=sys.stderr,
            )

        # _env_file=None (tests with injected transport) reads only process env,
        # not the real .env files.
        settings = Settings(_env_file=None) if self._transport else Settings()
        api_key = settings.serper_api_key
        if not api_key:
            # Name the files that were searched: the usual cause is running the
            # global CLI from a directory whose .env is not the one holding the key.
            searched = " or ".join(str(path) for path in env_files())
            raise ValueError(
                "serper adapter requires HORUS_SERPER_API_KEY env var. "
                f"Not found in the process env, {searched}. "
                "Set it in one of those files or export it. Never pass as a CLI flag."
            )

        items: list[ScrapedItem] = []
        async with httpx.AsyncClient(transport=self._transport, timeout=15) as client:
            for query in queries:
                body = await self._search(client, query, num, gl, hl, api_key)
                parsed = self.parse_response_json(body, query=query, gl=gl, hl=hl)
                items.extend(parsed[:requested])
        return items

    async def _search(
        self,
        client: httpx.AsyncClient,
        query: str,
        num: int,
        gl: str,
        hl: str,
        api_key: str,
    ) -> dict[str, Any]:
        payload = {"q": query, "gl": gl, "hl": hl, "num": num}
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

        resp = await client.post(_SERPER_URL, json=payload, headers=headers)

        if resp.status_code in (401, 403):
            raise ValueError(f"Invalid Serper API key or access denied (HTTP {resp.status_code})")
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                await asyncio.sleep(min(float(retry_after), 10))
                resp = await client.post(_SERPER_URL, json=payload, headers=headers)
            if resp.status_code == 429:
                raise RuntimeError("Serper rate limited (429)")
        if resp.status_code >= 500:
            await asyncio.sleep(1)
            resp = await client.post(_SERPER_URL, json=payload, headers=headers)

        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
