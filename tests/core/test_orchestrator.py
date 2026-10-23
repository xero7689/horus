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
        self.since_by_url: dict[str, datetime | None] = {}
        self.page_calls: list[str] = []

    async def __aenter__(self) -> "_FakeScraper":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def scrape(
        self,
        url: str,
        *_a: Any,
        limit: int = 0,
        since: datetime | None = None,
        on_batch: Any = None,
        **_k: Any,
    ):
        self.scrape_calls.append((url, limit))
        self.since_by_url[url] = since
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


class _ProfileStub(_InterceptStub):
    """Treats "prof:<name>" URLs as <name>'s profile; anything else is not a profile."""

    def profile_author(self, url: str) -> str | None:
        return url.removeprefix("prof:") if url.startswith("prof:") else None


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


def test_intercept_gives_every_url_the_full_limit(make_orch: Any) -> None:
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
    assert scraper.scrape_calls == [("u1", 5), ("u2", 5), ("u3", 5)]


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


def test_page_mode_fetches_every_url_regardless_of_limit(make_orch: Any) -> None:
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
    assert scraper.page_calls == ["p1", "p2", "p3"]


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


def _seed(path: Any, *authored: tuple[str, str, datetime]) -> None:
    storage = HorusStorage(path)
    storage.upsert_items(
        [
            ScrapedItem(
                id=i,
                site_id="intercept_stub",
                url=f"https://e.com/{i}",
                author_name=a,
                timestamp=ts,
            )
            for i, a, ts in authored
        ]
    )
    storage.close()


def test_intercept_incremental_since_is_per_profile_author(make_orch: Any, tmp_path: Any) -> None:
    alice_ts = datetime(2024, 6, 1, tzinfo=UTC)
    bob_ts = datetime(2024, 1, 1, tzinfo=UTC)
    _seed(tmp_path / "t.db", ("A1", "alice", alice_ts), ("B1", "bob", bob_ts))
    scraper = _FakeScraper({})
    asyncio.run(
        make_orch(scraper).run(
            _ProfileStub(),
            ["prof:bob", "prof:carol", "thread:x"],
            site="intercept_stub",
            kwargs={},
            limit=5,
            since=None,
            state_path=None,
        )
    )
    # bob resumes from his own newest post, not alice's newer one; carol has
    # nothing stored; a non-profile URL (thread/status) gets no cutoff at all.
    assert scraper.since_by_url == {"prof:bob": bob_ts, "prof:carol": None, "thread:x": None}


def test_intercept_explicit_since_applies_to_every_url(make_orch: Any, tmp_path: Any) -> None:
    _seed(tmp_path / "t.db", ("B1", "bob", datetime(2024, 1, 1, tzinfo=UTC)))
    explicit = datetime(2020, 1, 1, tzinfo=UTC)
    scraper = _FakeScraper({})
    asyncio.run(
        make_orch(scraper).run(
            _ProfileStub(),
            ["prof:bob", "thread:x"],
            site="intercept_stub",
            kwargs={},
            limit=5,
            since=explicit,
            state_path=None,
        )
    )
    assert scraper.since_by_url == {"prof:bob": explicit, "thread:x": explicit}
