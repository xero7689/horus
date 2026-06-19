"""Tests for CLI _export_thread_md helper (orphan skipping + id sanitization)."""

from datetime import UTC, datetime
from pathlib import Path

from horus.cli import _export_thread_md
from horus.models import ScrapedItem


def _item(
    item_id: str,
    *,
    author: str = "alice",
    minute: int = 0,
    is_reply: bool = False,
    parent: str | None = None,
    conv: str | None = None,
) -> ScrapedItem:
    extra: dict[str, object] = {"is_reply": is_reply}
    if parent:
        extra["parent_post_id"] = parent
    if conv:
        extra["conversation_id"] = conv
    return ScrapedItem(
        id=item_id,
        site_id="threads",
        url=f"https://threads.net/t/{item_id}",
        author_name=author,
        text="hi",
        timestamp=datetime(2026, 1, 1, 14, minute, tzinfo=UTC),
        extra=extra,
    )


def test_skips_orphan_reply_groups(tmp_path: Path) -> None:
    # No true root in input — just an orphan reply pointing at a missing parent
    items = [_item("r1", is_reply=True, parent="missing")]
    written = _export_thread_md(items, tmp_path)
    assert written == 0
    assert list(tmp_path.iterdir()) == []


def test_writes_true_root_only(tmp_path: Path) -> None:
    items = [
        _item("p1"),  # true root
        _item("r1", minute=5, is_reply=True, parent="p1", conv="p1"),
        _item("orphan", minute=10, is_reply=True, parent="missing"),
    ]
    written = _export_thread_md(items, tmp_path)
    assert written == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "alice_p1.md"


def test_sanitizes_dirty_post_id(tmp_path: Path) -> None:
    items = [_item("a/b/c")]
    _export_thread_md(items, tmp_path)
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    # no path separator slipped through
    assert "/" not in files[0].name[len("alice_") :]
    assert files[0].name.startswith("alice_")
