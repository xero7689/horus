"""Tests for thread tree building and markdown rendering."""

from datetime import UTC, datetime

from horus.core.thread_tree import build_thread_trees, render_thread_md
from horus.models import ScrapedItem


def _item(
    item_id: str,
    text: str,
    *,
    author: str = "alice",
    minute: int = 0,
    is_reply: bool = False,
    parent: str | None = None,
    conv: str | None = None,
    reply_to: str | None = None,
) -> ScrapedItem:
    extra: dict[str, object] = {"is_reply": is_reply}
    if parent:
        extra["parent_post_id"] = parent
    if conv:
        extra["conversation_id"] = conv
    if reply_to:
        extra["reply_to_username"] = reply_to
    return ScrapedItem(
        id=item_id,
        site_id="threads",
        url=f"https://threads.net/t/{item_id}",
        author_name=author,
        text=text,
        timestamp=datetime(2026, 1, 1, 14, minute, tzinfo=UTC),
        extra=extra,
    )


class TestBuildThreadTrees:
    def test_single_root_no_replies(self) -> None:
        items = [_item("p1", "hello")]
        roots = build_thread_trees(items)
        assert len(roots) == 1
        assert roots[0].item.id == "p1"
        assert roots[0].children == []

    def test_nested_replies(self) -> None:
        items = [
            _item("p1", "root"),
            _item("r1", "reply1", minute=5, is_reply=True, parent="p1", conv="p1"),
            _item("r2", "reply2", minute=10, is_reply=True, parent="r1", conv="p1"),
            _item("r3", "reply3", minute=15, is_reply=True, parent="p1", conv="p1"),
        ]
        roots = build_thread_trees(items)
        assert len(roots) == 1
        root = roots[0]
        assert len(root.children) == 2
        assert root.children[0].item.id == "r1"
        assert root.children[1].item.id == "r3"
        assert root.children[0].children[0].item.id == "r2"

    def test_siblings_sorted_by_timestamp(self) -> None:
        items = [
            _item("p1", "root"),
            _item("b", "later", minute=20, is_reply=True, parent="p1"),
            _item("a", "earlier", minute=5, is_reply=True, parent="p1"),
        ]
        roots = build_thread_trees(items)
        ids = [c.item.id for c in roots[0].children]
        assert ids == ["a", "b"]

    def test_orphan_reply_falls_back_to_conversation(self) -> None:
        items = [
            _item("p1", "root"),
            _item(
                "r1",
                "orphan",
                minute=5,
                is_reply=True,
                parent="missing",
                conv="p1",
            ),
        ]
        roots = build_thread_trees(items)
        assert roots[0].children[0].item.id == "r1"

    def test_multiple_conversations(self) -> None:
        items = [
            _item("p1", "root1"),
            _item("p2", "root2", minute=30),
            _item("r1", "reply to p1", minute=5, is_reply=True, parent="p1", conv="p1"),
        ]
        roots = build_thread_trees(items)
        assert [r.item.id for r in roots] == ["p1", "p2"]
        assert len(roots[0].children) == 1
        assert len(roots[1].children) == 0


class TestRenderThreadMd:
    def test_no_replies(self) -> None:
        items = [_item("p1", "hello world")]
        out = render_thread_md(build_thread_trees(items)[0])
        assert "# @alice" in out
        assert "hello world" in out
        assert "## 留言" not in out

    def test_with_nested_replies(self) -> None:
        items = [
            _item("p1", "root post"),
            _item(
                "r1",
                "first reply",
                author="bob",
                minute=5,
                is_reply=True,
                parent="p1",
                conv="p1",
            ),
            _item(
                "r2",
                "nested reply",
                author="carol",
                minute=10,
                is_reply=True,
                parent="r1",
                conv="p1",
                reply_to="bob",
            ),
        ]
        out = render_thread_md(build_thread_trees(items)[0])
        assert "## 留言" in out
        assert "- @bob" in out
        assert "  - @carol" in out
        assert "> 回覆 @bob" in out
