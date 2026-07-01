"""CLI-layer tests for the Instagram adapter interception comment path.

Covers:
1. --with-comments uses scrape() intercept, never scrape_comments() (test 1)
2. Mixed primary batch: comment leaked into primary batch is deferred to backfill (test 2)
3. Backfill attribution filter: foreign-post comments are rejected (test 3)
4. Without --with-comments: comments leaking into primary batch are silently dropped (test 4)
"""

import json
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
    return ScrapedItem(
        id=_POST_ID,
        site_id="stub_ig",
        url=_POST_URL,
        text="post text",
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
    backfill_items: list[ScrapedItem] | None = None,
) -> MagicMock:
    """Return a BaseScraper context-manager mock.

    First ``scrape()`` call (primary crawl) fires ``on_batch`` with
    ``primary_batch`` and returns the same list.  Subsequent calls
    (comment backfill) return ``backfill_items`` without calling
    ``on_batch``.
    """
    call_count = 0
    _backfill = backfill_items or []

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
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Primary crawl — simulate batched delivery via on_batch callback
            if on_batch and primary_batch:
                on_batch(list(primary_batch))
            return list(primary_batch)
        # Comment backfill call — no on_batch, just return items
        return list(_backfill)

    scraper = MagicMock()
    scraper.__aenter__ = AsyncMock(return_value=scraper)
    scraper.__aexit__ = AsyncMock(return_value=None)
    scraper.scrape = AsyncMock(side_effect=_scrape)
    scraper.scrape_comments = AsyncMock(return_value=[])
    return scraper


# ---------------------------------------------------------------------------
# Helper: recording upsert side-effect (used in test 2)
# ---------------------------------------------------------------------------


def _make_recording_upsert(
    captured: list[list[ScrapedItem]],
) -> Callable[[HorusStorage, list[ScrapedItem]], int]:
    """Return an upsert_items replacement that records batches and persists for real."""

    def _upsert(self: HorusStorage, items: list[ScrapedItem]) -> int:
        # Deep-copy before mutation so assertions see pre-backfill state
        captured.append([ScrapedItem(**i.model_dump()) for i in items])
        # Replicate real persistence so the DB is consistent
        new_count = 0
        for item in items:
            existing = self._conn.execute(
                "SELECT 1 FROM items WHERE site_id = ? AND id = ?",
                (item.site_id, item.id),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """UPDATE items
                    SET text = ?, url = ?, author_id = ?, author_name = ?,
                        extra = ?, fetched_at = datetime('now')
                    WHERE site_id = ? AND id = ?""",
                    (
                        item.text,
                        item.url,
                        item.author_id,
                        item.author_name,
                        json.dumps(item.extra),
                        item.site_id,
                        item.id,
                    ),
                )
            else:
                self._conn.execute(
                    """INSERT INTO items
                    (id, site_id, url, text, author_id, author_name, timestamp, extra)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.site_id,
                        item.url,
                        item.text,
                        item.author_id,
                        item.author_name,
                        item.timestamp.isoformat(),
                        json.dumps(item.extra),
                    ),
                )
                new_count += 1
        self._conn.commit()
        return new_count

    return _upsert


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ig_with_comments_uses_interception(monkeypatch: Any, tmp_path: Any) -> None:
    """Primary crawl yields a post; comment backfill via scrape() not scrape_comments().

    Assert:
    - scrape_comments() is NEVER called
    - scrape() is called exactly twice (primary + one comment backfill)
    - both post and comment land in DB
    - comment.url is backfilled to post.url
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post()],
        backfill_items=[_comment()],  # scrape() returns comment on second call
    )

    with patch("horus.cli.BaseScraper", return_value=scraper_mock):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", _POST_URL, "--with-comments"],
        )

    assert result.exit_code == 0, result.output

    # scrape_comments must NOT have been called — intercept path only
    scraper_mock.scrape_comments.assert_not_called()

    # scrape() called twice: primary crawl + comment backfill
    assert scraper_mock.scrape.call_count == 2

    # Check DB: both post and comment should be stored
    storage = HorusStorage(tmp_path / "t.db")
    items = storage.get_items(site_id="stub_ig", limit=100)
    storage.close()

    ids = {item.id for item in items}
    assert _POST_ID in ids, "post should be stored"
    assert _CMT_ID in ids, "comment should be stored after backfill"

    # Comment url must be backfilled from post
    stored_comment = next(i for i in items if i.id == _CMT_ID)
    assert stored_comment.url == _POST_URL, (
        f"comment url should be backfilled to {_POST_URL!r}, got {stored_comment.url!r}"
    )


def test_ig_mixed_page_defers_comments(monkeypatch: Any, tmp_path: Any) -> None:
    """KEY test (codex finding 1+4): mixed primary batch defers comment to backfill.

    The primary on_batch receives [post, comment].  The comment MUST be dropped
    from the first upsert_items call and only land via the --with-comments
    backfill loop with url backfilled.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post(), _comment()],  # comment leaks into primary batch
        backfill_items=[_comment()],  # comment available again when re-scraping post page
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

    # Must have had at least two upsert_items calls: primary + backfill
    assert len(captured) >= 2, f"expected ≥2 upsert calls, got {len(captured)}: {captured}"

    # First upsert: only post — comment MUST have been filtered out by on_batch guard
    first_ids = {i.id for i in captured[0]}
    assert _POST_ID in first_ids, "post should be in first upsert"
    assert _CMT_ID not in first_ids, (
        "comment must NOT appear in primary-crawl upsert — it should be deferred"
    )

    # Second upsert: comment with backfilled url
    second_ids = {i.id for i in captured[1]}
    assert _CMT_ID in second_ids, "comment should appear in backfill upsert"

    backfill_comment = next(i for i in captured[1] if i.id == _CMT_ID)
    assert backfill_comment.url == _POST_URL, (
        f"comment url must be backfilled to {_POST_URL!r}, got {backfill_comment.url!r}"
    )


def test_ig_backfill_rejects_foreign_comment(monkeypatch: Any, tmp_path: Any) -> None:
    """Attribution filter: only comments whose parent_post_id matches post.id are kept.

    The comment scrape returns two replies — one for this post, one for another.
    Only the matching one should land in DB with backfilled url.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post()],
        backfill_items=[_comment(), _foreign_comment()],  # two replies; only one belongs here
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
    """Without --with-comments, comments leaked into primary batch are silently dropped.

    scrape() is called only once (no backfill loop).  Only the post lands in DB.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))

    scraper_mock = _build_scraper_mock(
        primary_batch=[_post(), _comment()],  # comment leaks in
        # backfill_items unused — scrape() should not be called a second time
    )

    with patch("horus.cli.BaseScraper", return_value=scraper_mock):
        result = CliRunner().invoke(
            main,
            ["crawl", "stub_ig", "--url", _POST_URL],  # NO --with-comments
        )

    assert result.exit_code == 0, result.output

    # scrape() called exactly once — no backfill loop without --with-comments
    assert scraper_mock.scrape.call_count == 1, (
        "scrape() should only be called once (no backfill without --with-comments)"
    )
    scraper_mock.scrape_comments.assert_not_called()

    # Only the post should be in DB; comment must have been dropped
    storage = HorusStorage(tmp_path / "t.db")
    items = storage.get_items(site_id="stub_ig", limit=100)
    storage.close()

    ids = {item.id for item in items}
    assert _POST_ID in ids, "post should be stored"
    assert _CMT_ID not in ids, "comment must NOT be stored without --with-comments"
