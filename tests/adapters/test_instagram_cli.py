"""CLI-layer tests for the Instagram adapter interception comment path.

Instagram comments only fire when a post is opened as an in-app MODAL from the profile
grid (direct /p/<code>/ navigation inlines the post and fires nothing). So the CLI fetches
comments by calling ``scraper.scrape_comments_via_grid(profile_url, ...)`` once per profile
(derived from each crawled post's ``author_name``), then attributes comments to posts by
``parent_post_id == post.id`` and backfills the comment URL from the parent post.

Covers:
1. --with-comments uses scrape_comments_via_grid(), never the HTML scrape_comments() (test 1)
2. Mixed primary batch: a comment leaked into the primary crawl is dropped by the on_batch
   guard and only lands via the grid-walk backfill (test 2)
3. Attribution: comments whose parent_post_id doesn't match a crawled post are rejected (test 3)
4. Without --with-comments: comments leaking into the primary batch are silently dropped (test 4)
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from horus.adapters import _REGISTRY
from horus.adapters.base import SiteAdapter
from horus.cli import main
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TS = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_HANDLE = "nasa"
_POST_URL = "https://www.instagram.com/p/abc123/"
_POST_ID = "POST001"
_CMT_ID = "CMT001"
_FOREIGN_CMT_ID = "CMT002"


# ---------------------------------------------------------------------------
# Stub adapter (comments_via_interception = True)
# ---------------------------------------------------------------------------


class _StubIgAdapter(SiteAdapter):
    """Minimal IG-style adapter for CLI routing tests."""

    site_id = "stub_ig"
    display_name = "Stub IG"
    login_url = "https://example.com/login"
    requires_login = False
    comments_via_interception = True

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        return lambda url, body: True

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        return []

    def get_urls(self, **kwargs: Any) -> list[str]:
        url = kwargs.get("url")
        if url:
            return [url]
        raise ValueError("stub_ig requires --url")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def register_stub_adapter() -> Any:
    """Register the stub IG adapter for the duration of each test."""
    _REGISTRY["stub_ig"] = _StubIgAdapter
    yield
    _REGISTRY.pop("stub_ig", None)


# ---------------------------------------------------------------------------
# Item factories
# ---------------------------------------------------------------------------


def _post() -> ScrapedItem:
    # author_name is required: the CLI derives the profile URL for the grid walk from it.
    return ScrapedItem(
        id=_POST_ID,
        site_id="stub_ig",
        url=_POST_URL,
        text="post text",
        author_name=_HANDLE,
        timestamp=_TS,
        extra={"is_reply": False, "parent_post_id": None},
    )


def _comment(parent_id: str = _POST_ID) -> ScrapedItem:
    return ScrapedItem(
        id=_CMT_ID,
        site_id="stub_ig",
        url="",  # no shortcode in comment body — must be backfilled
        text="comment text",
        timestamp=_TS,
        extra={"is_reply": True, "parent_post_id": parent_id},
    )


def _foreign_comment() -> ScrapedItem:
    return ScrapedItem(
        id=_FOREIGN_CMT_ID,
        site_id="stub_ig",
        url="",
        text="foreign comment",
        timestamp=_TS,
        extra={"is_reply": True, "parent_post_id": "SOME_OTHER_POST"},
    )


# ---------------------------------------------------------------------------
# Scraper mock builder
# ---------------------------------------------------------------------------


def _build_scraper_mock(
    *,
    primary_batch: list[ScrapedItem],
    grid_items: list[ScrapedItem] | None = None,
) -> MagicMock:
    """Return a BaseScraper context-manager mock.

    ``scrape()`` (the primary crawl) fires ``on_batch`` with ``primary_batch`` and returns
    it.  ``scrape_comments_via_grid()`` (the modal walk) returns ``grid_items`` — the
    posts + comments the modal walk would have intercepted.
    """
    _grid = grid_items or []

    async def _scrape(
        url: str,
        response_filter: Any,
        parser: Any,
        state_path: Any = None,
        *,
        since: Any = None,
        on_progress: Any = None,
        on_batch: Any = None,
    ) -> list[ScrapedItem]:
        if on_batch and primary_batch:
            on_batch(list(primary_batch))
        return list(primary_batch)

    async def _grid_walk(
        profile_url: str,
        response_filter: Any,
        parser: Any,
        state_path: Any = None,
        *,
        max_posts: int = 12,
        thumbnail_selector: str = "",
        on_progress: Any = None,
    ) -> list[ScrapedItem]:
        return list(_grid)

    scraper = MagicMock()
    scraper.__aenter__ = AsyncMock(return_value=scraper)
    scraper.__aexit__ = AsyncMock(return_value=None)
    scraper.scrape = AsyncMock(side_effect=_scrape)
    scraper.scrape_comments = AsyncMock(return_value=[])
    scraper.scrape_comments_via_grid = AsyncMock(side_effect=_grid_walk)
    return scraper


# ---------------------------------------------------------------------------
# Helper: recording upsert side-effect (used in test 2)
# ---------------------------------------------------------------------------


def _make_recording_upsert(
    captured: list[list[ScrapedItem]],
) -> Callable[[HorusStorage, list[ScrapedItem]], int]:
    """Return a call-through upsert_items wrapper that records each batch.

    Snapshots the items at call time (Pydantic objects are mutable, so we copy
    to avoid aliasing later mutations) then delegates to the real method, so the
    DB stays consistent and the SQL stays in one place.
    """
    _orig_upsert = HorusStorage.upsert_items

    def _upsert(self: HorusStorage, items: list[ScrapedItem]) -> int:
        captured.append([ScrapedItem(**i.model_dump()) for i in items])
        return _orig_upsert(self, items)

    return _upsert


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ig_with_comments_uses_grid_walk(monkeypatch: Any, tmp_path: Any) -> None:
    """Comments are fetched via scrape_comments_via_grid(), never the HTML scrape_comments().

    Assert:
    - scrape_comments() is NEVER called
    - scrape_comments_via_grid() is called once, with the derived profile URL
    - both post and comment land in DB
    - comment.url is backfilled to post.url
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post()],
        grid_items=[_comment()],
    )

    with patch("horus.cli.BaseScraper", return_value=scraper_mock):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", _POST_URL, "--with-comments"],
        )

    assert result.exit_code == 0, result.output

    # HTML comment path must NOT be used
    scraper_mock.scrape_comments.assert_not_called()

    # Grid walk called once, with the profile URL derived from post.author_name
    scraper_mock.scrape_comments_via_grid.assert_called_once()
    profile_url = scraper_mock.scrape_comments_via_grid.call_args.args[0]
    assert _HANDLE in profile_url, f"profile url should target @{_HANDLE}, got {profile_url!r}"

    storage = HorusStorage(tmp_path / "t.db")
    items = storage.get_items(site_id="stub_ig", limit=100)
    storage.close()

    ids = {item.id for item in items}
    assert _POST_ID in ids, "post should be stored"
    assert _CMT_ID in ids, "comment should be stored after grid walk"

    stored_comment = next(i for i in items if i.id == _CMT_ID)
    assert stored_comment.url == _POST_URL, (
        f"comment url should be backfilled to {_POST_URL!r}, got {stored_comment.url!r}"
    )


def test_ig_mixed_page_defers_comments(monkeypatch: Any, tmp_path: Any) -> None:
    """KEY test: a comment leaked into the primary batch is deferred to the grid-walk backfill.

    The primary on_batch receives [post, comment].  The comment MUST be dropped from the
    first upsert_items call (on_batch guard) and only land via the grid-walk backfill with
    url backfilled.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post(), _comment()],  # comment leaks into primary batch
        grid_items=[_comment()],  # comment intercepted again by the modal walk
    )

    captured: list[list[ScrapedItem]] = []
    recording_upsert = _make_recording_upsert(captured)

    with (
        patch("horus.cli.BaseScraper", return_value=scraper_mock),
        patch.object(HorusStorage, "upsert_items", autospec=True, side_effect=recording_upsert),
    ):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", _POST_URL, "--with-comments"],
        )

    assert result.exit_code == 0, result.output
    assert len(captured) >= 2, f"expected ≥2 upsert calls, got {len(captured)}: {captured}"

    # First upsert: only post — comment MUST have been filtered out by the on_batch guard
    first_ids = {i.id for i in captured[0]}
    assert _POST_ID in first_ids, "post should be in first upsert"
    assert _CMT_ID not in first_ids, (
        "comment must NOT appear in primary-crawl upsert — it should be deferred"
    )

    # A later upsert: comment with backfilled url
    later_ids = {i.id for batch in captured[1:] for i in batch}
    assert _CMT_ID in later_ids, "comment should appear in a backfill upsert"
    backfill_comment = next(i for batch in captured[1:] for i in batch if i.id == _CMT_ID)
    assert backfill_comment.url == _POST_URL, (
        f"comment url must be backfilled to {_POST_URL!r}, got {backfill_comment.url!r}"
    )


def test_ig_grid_walk_rejects_foreign_comment(monkeypatch: Any, tmp_path: Any) -> None:
    """Attribution filter: only comments whose parent_post_id matches a crawled post are kept.

    The grid walk returns two replies — one for this post, one for another. Only the
    matching one should land in DB with backfilled url.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post()],
        grid_items=[_comment(), _foreign_comment()],  # two replies; only one belongs here
    )

    with patch("horus.cli.BaseScraper", return_value=scraper_mock):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", _POST_URL, "--with-comments"],
        )

    assert result.exit_code == 0, result.output

    storage = HorusStorage(tmp_path / "t.db")
    items = storage.get_items(site_id="stub_ig", limit=100)
    storage.close()

    ids = {item.id for item in items}
    assert _POST_ID in ids, "post should be stored"
    assert _CMT_ID in ids, "matching comment should be stored"
    assert _FOREIGN_CMT_ID not in ids, "foreign comment must be rejected (different parent_post_id)"

    stored_comment = next(i for i in items if i.id == _CMT_ID)
    assert stored_comment.url == _POST_URL, (
        f"matching comment url must be backfilled to {_POST_URL!r}"
    )


def test_ig_no_with_comments_drops_comments(monkeypatch: Any, tmp_path: Any) -> None:
    """Without --with-comments, comments leaked into the primary batch are silently dropped.

    The grid walk is never invoked; only the post lands in DB.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post(), _comment()],  # comment leaks in
    )

    with patch("horus.cli.BaseScraper", return_value=scraper_mock):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", _POST_URL],  # NO --with-comments
        )

    assert result.exit_code == 0, result.output

    # No comment fetch at all without --with-comments
    scraper_mock.scrape_comments_via_grid.assert_not_called()
    scraper_mock.scrape_comments.assert_not_called()

    storage = HorusStorage(tmp_path / "t.db")
    items = storage.get_items(site_id="stub_ig", limit=100)
    storage.close()

    ids = {item.id for item in items}
    assert _POST_ID in ids, "post should be stored"
    assert _CMT_ID not in ids, "comment must NOT be stored without --with-comments"


def test_ig_multi_profile_walks_each_profile(monkeypatch: Any, tmp_path: Any) -> None:
    """Two crawled posts from different profiles → one grid walk per profile, each
    attributing its own comments. Locks in the multi-profile grouping fix.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    post_a = ScrapedItem(
        id="POSTA",
        site_id="stub_ig",
        url="https://www.instagram.com/p/aaa/",
        author_name="nasa",
        timestamp=_TS,
        extra={"is_reply": False, "parent_post_id": None},
    )
    post_b = ScrapedItem(
        id="POSTB",
        site_id="stub_ig",
        url="https://www.instagram.com/p/bbb/",
        author_name="spacex",
        timestamp=_TS,
        extra={"is_reply": False, "parent_post_id": None},
    )
    cmt_a = ScrapedItem(
        id="CMTA",
        site_id="stub_ig",
        url="",
        timestamp=_TS,
        extra={"is_reply": True, "parent_post_id": "POSTA"},
    )
    cmt_b = ScrapedItem(
        id="CMTB",
        site_id="stub_ig",
        url="",
        timestamp=_TS,
        extra={"is_reply": True, "parent_post_id": "POSTB"},
    )

    async def _scrape(
        url,
        response_filter,
        parser,
        state_path=None,
        *,
        since=None,
        on_progress=None,
        on_batch=None,
    ):  # type: ignore[no-untyped-def]
        if on_batch:
            on_batch([post_a, post_b])
        return [post_a, post_b]

    async def _grid_walk(
        profile_url,
        response_filter,
        parser,
        state_path=None,
        *,
        max_posts=12,
        thumbnail_selector="",
        on_progress=None,
    ):  # type: ignore[no-untyped-def]
        # return each profile's own comment
        if "nasa" in profile_url:
            return [cmt_a]
        if "spacex" in profile_url:
            return [cmt_b]
        return []

    scraper_mock = MagicMock()
    scraper_mock.__aenter__ = AsyncMock(return_value=scraper_mock)
    scraper_mock.__aexit__ = AsyncMock(return_value=None)
    scraper_mock.scrape = AsyncMock(side_effect=_scrape)
    scraper_mock.scrape_comments = AsyncMock(return_value=[])
    scraper_mock.scrape_comments_via_grid = AsyncMock(side_effect=_grid_walk)

    with patch("horus.cli.BaseScraper", return_value=scraper_mock):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", "https://www.instagram.com/p/aaa/", "--with-comments"],
        )

    assert result.exit_code == 0, result.output

    # one grid walk per profile, each with the right profile URL
    assert scraper_mock.scrape_comments_via_grid.call_count == 2
    walked = {call.args[0] for call in scraper_mock.scrape_comments_via_grid.call_args_list}
    assert any("nasa" in u for u in walked) and any("spacex" in u for u in walked)

    storage = HorusStorage(tmp_path / "t.db")
    items = storage.get_items(site_id="stub_ig", limit=100)
    storage.close()
    by_id = {i.id: i for i in items}

    assert "CMTA" in by_id and by_id["CMTA"].url == "https://www.instagram.com/p/aaa/"
    assert "CMTB" in by_id and by_id["CMTB"].url == "https://www.instagram.com/p/bbb/"
