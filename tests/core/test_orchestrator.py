"""Smoke: orchestrator http-mode path with a stub adapter and NullReporter."""

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.orchestrator import CrawlOrchestrator
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


class _HttpStub(SiteAdapter):
    site_id = "http_stub"
    display_name = "HTTP Stub"
    login_url = ""
    requires_login = False
    has_http_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        assert kwargs["limit"] == 7  # top-level limit forwarded
        return [
            ScrapedItem(
                id="X1",
                site_id="http_stub",
                url="https://e.com/1",
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            )
        ]


def test_http_mode_forwards_limit_and_persists(tmp_path: Any) -> None:
    storage = HorusStorage(tmp_path / "t.db")
    orch = CrawlOrchestrator(storage, Settings())
    outcome = asyncio.run(
        orch.run(
            _HttpStub(),
            [],
            site="http_stub",
            kwargs={},
            limit=7,
            since=None,
            state_path=None,
        )
    )
    assert (outcome.total_found, outcome.total_new) == (1, 1)
    assert storage.get_items(site_id="http_stub", limit=10)[0].id == "X1"
    storage.close()


class _FakeScraper:
    """Records scrape()/scrape_page() calls; returns canned items per URL."""

    def __init__(self, per_url: dict[str, list[ScrapedItem]]) -> None:
        self.per_url = per_url
        self.scrape_calls: list[tuple[str, int]] = []
        self.page_calls: list[str] = []

    async def __aenter__(self) -> "_FakeScraper":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def scrape(self, url: str, *_a: Any, limit: int = 0, on_batch: Any = None, **_k: Any):
        self.scrape_calls.append((url, limit))
        items = self.per_url.get(url, [])
        if on_batch and items:
            on_batch(items)
        return items

    async def scrape_page(self, url: str, *_a: Any, site_id: str, **_k: Any):
        from horus.models import ScrapedPage

        self.page_calls.append(url)
        return ScrapedPage(url=url, site_id=site_id, markdown="# x")


def _items(prefix: str, n: int) -> list[ScrapedItem]:
    return [
        ScrapedItem(
            id=f"{prefix}{i}",
            site_id="intercept_stub",
            url=f"https://e.com/{prefix}{i}",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )
        for i in range(n)
    ]


class _InterceptStub(SiteAdapter):
    site_id = "intercept_stub"
    display_name = "Intercept Stub"
    login_url = ""
    requires_login = False

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []


class _PageStub(SiteAdapter):
    site_id = "page_stub"
    display_name = "Page Stub"
    login_url = ""
    requires_login = False
    has_page_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []


@pytest.fixture
def make_orch(tmp_path: Any) -> Iterator[Callable[[_FakeScraper], CrawlOrchestrator]]:
    storages: list[HorusStorage] = []

    def _make(scraper: _FakeScraper) -> CrawlOrchestrator:
        storage = HorusStorage(tmp_path / "t.db")
        storages.append(storage)
        return CrawlOrchestrator(storage, Settings(), scraper_cls=lambda **_k: scraper)  # type: ignore[arg-type,return-value]

    yield _make
    for storage in storages:
        storage.close()


def test_intercept_passes_remaining_quota_per_url(make_orch: Any) -> None:
    scraper = _FakeScraper({"u1": _items("a", 3), "u2": _items("b", 3)})
    asyncio.run(
        make_orch(scraper).run(
            _InterceptStub(),
            ["u1", "u2", "u3"],
            site="intercept_stub",
            kwargs={},
            limit=5,
            since=None,
            state_path=None,
        )
    )
    # u1 gets the full 5, returns 3 → u2 gets 2, returns 3 → quota spent, u3 skipped
    assert scraper.scrape_calls == [("u1", 5), ("u2", 2)]


def test_intercept_limit_zero_is_unlimited(make_orch: Any) -> None:
    scraper = _FakeScraper({"u1": _items("a", 3), "u2": _items("b", 3)})
    asyncio.run(
        make_orch(scraper).run(
            _InterceptStub(),
            ["u1", "u2"],
            site="intercept_stub",
            kwargs={},
            limit=0,
            since=None,
            state_path=None,
        )
    )
    assert scraper.scrape_calls == [("u1", 0), ("u2", 0)]


def test_page_mode_fetches_only_first_limit_urls(make_orch: Any) -> None:
    scraper = _FakeScraper({})
    asyncio.run(
        make_orch(scraper).run(
            _PageStub(),
            ["p1", "p2", "p3"],
            site="page_stub",
            kwargs={},
            limit=2,
            since=None,
            state_path=None,
        )
    )
    assert scraper.page_calls == ["p1", "p2"]


def test_page_mode_limit_zero_fetches_all(make_orch: Any) -> None:
    scraper = _FakeScraper({})
    asyncio.run(
        make_orch(scraper).run(
            _PageStub(),
            ["p1", "p2", "p3"],
            site="page_stub",
            kwargs={},
            limit=0,
            since=None,
            state_path=None,
        )
    )
    assert scraper.page_calls == ["p1", "p2", "p3"]


def _unattributed(item_id: str) -> ScrapedItem:
    return ScrapedItem(
        id=item_id,
        site_id="intercept_stub",
        url="",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        extra={"is_reply": True},
    )


class _PostStub(_InterceptStub):
    """is_post_url('post1') -> single-post path; parse_response yields 2 kept + 1 dropped."""

    def is_post_url(self, url: str) -> bool:
        return url == "post1"

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        return [*_items("p", 2), _unattributed("c0")]


class _InlineScraper(_FakeScraper):
    async def fetch_inline_json(self, url: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        return {}


def test_single_post_quota_counts_only_kept_items(make_orch: Any) -> None:
    scraper = _InlineScraper({"u2": _items("b", 1)})
    asyncio.run(
        make_orch(scraper).run(
            _PostStub(),
            ["post1", "u2"],
            site="intercept_stub",
            kwargs={},
            limit=3,
            since=None,
            state_path=None,
        )
    )
    # post1 yields 2 kept + 1 unattributed comment dropped by on_batch → 3 - 2 = 1
    assert scraper.scrape_calls == [("u2", 1)]


def test_scroll_quota_counts_only_kept_items(make_orch: Any) -> None:
    scraper = _FakeScraper({"u1": [*_items("a", 2), _unattributed("c0")], "u2": _items("b", 1)})
    asyncio.run(
        make_orch(scraper).run(
            _InterceptStub(),
            ["u1", "u2"],
            site="intercept_stub",
            kwargs={},
            limit=3,
            since=None,
            state_path=None,
        )
    )
    assert scraper.scrape_calls == [("u1", 3), ("u2", 1)]
