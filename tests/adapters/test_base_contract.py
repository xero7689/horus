"""Locks the SiteAdapter contract: page/http adapters need no interception stubs."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from horus.adapters import get_adapter
from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem


class _PageOnly(SiteAdapter):
    site_id = "page_only"
    display_name = "Page Only"
    login_url = "https://example.com/login"
    requires_login = False
    has_page_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return ["https://example.com"]


def test_page_adapter_needs_no_interception_stubs() -> None:
    a = _PageOnly()  # must instantiate without overriding interception methods
    assert a.parse_response({}) == []
    f: Callable[[str, dict[str, Any]], bool] = a.get_response_filter()
    assert f("https://example.com", {}) is False


def test_page_mode_hooks_declared_with_none_defaults() -> None:
    assert SiteAdapter.wait_for_selector is None
    assert SiteAdapter.page_script is None
    assert SiteAdapter.content_script is None


def test_default_fetch_comments_uses_comment_parser() -> None:
    """Default strategy: per-post scrape_comments() with get_comment_parser()."""

    class _WithParser(_PageOnly):
        site_id = "with_parser"

        def get_comment_parser(self):  # type: ignore[override]
            return lambda html, *, post_pk: []

    a = _WithParser()
    post = ScrapedItem(
        id="P1",
        site_id="with_parser",
        url="https://e.com/p/1",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )
    scraper = MagicMock()
    scraper.scrape_comments = AsyncMock(return_value=[])

    async def collect():
        return [pair async for pair in a.fetch_comments(scraper, [post], None)]

    pairs = asyncio.run(collect())
    assert pairs == [(post, [])]
    scraper.scrape_comments.assert_awaited_once()


def test_default_fetch_comments_without_parser_yields_empty() -> None:
    a = _PageOnly()  # get_comment_parser() -> None
    scraper = MagicMock()

    async def collect():
        return [pair async for pair in a.fetch_comments(scraper, [], None)]

    assert asyncio.run(collect()) == []


_EXPECTED_OPTIONS = {
    "threads": {"--user", "--url", "--mode", "--with-comments"},
    "twitter": {"--user", "--url", "--mode"},
    "instagram": {"--user", "--url", "--with-comments"},
    "web": {"--url", "--url-list"},
    "facebook": {"--url"},
    "serper": {"--query", "--gl", "--hl"},
    "ddg": {"--query"},
}


@pytest.mark.parametrize("site", sorted(_EXPECTED_OPTIONS))
def test_crawl_options_match_what_get_urls_and_orchestrator_accept(site: str) -> None:
    names = {opt["name"] for opt in get_adapter(site)().get_crawl_options()}
    assert names == _EXPECTED_OPTIONS[site]
