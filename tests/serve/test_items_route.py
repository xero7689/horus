"""Tests for /items routes — covers search vs list branching, delete, and 404."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


def _make_item(item_id: str, text: str, author: str = "testuser") -> ScrapedItem:
    return ScrapedItem(
        id=item_id,
        site_id="threads",
        url=f"https://threads.net/t/{item_id}",
        author_name=author,
        text=text,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestListItems:
    def test_returns_html(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items([_make_item("1", "hello world")])
        resp = client.get("/items")
        assert resp.status_code == 200
        assert "hello world" in resp.text

    def test_filter_by_site(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items([_make_item("1", "threads post")])
        resp = client.get("/items?site=threads")
        assert resp.status_code == 200
        assert "threads post" in resp.text

    def test_filter_by_site_no_results(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items([_make_item("1", "threads post")])
        resp = client.get("/items?site=twitter")
        assert resp.status_code == 200
        assert "threads post" not in resp.text

    def test_search_query(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items(
            [
                _make_item("1", "apple banana cherry"),
                _make_item("2", "dog elephant fox"),
            ]
        )
        resp = client.get("/items?q=banana")
        assert resp.status_code == 200
        assert "apple banana cherry" in resp.text
        assert "dog elephant fox" not in resp.text

    def test_htmx_returns_partial(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items([_make_item("1", "partial test")])
        resp = client.get("/items", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        # Partial should not contain full page shell (e.g. <html>)
        assert "<html" not in resp.text
        assert "partial test" in resp.text


class TestItemDetail:
    def test_returns_404_for_missing(self, client: TestClient) -> None:
        resp = client.get("/items/threads/missing-id")
        assert resp.status_code == 404

    def test_renders_post_without_replies(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items([_make_item("p1", "lonely post")])
        resp = client.get("/items/threads/p1")
        assert resp.status_code == 200
        assert "lonely post" in resp.text
        assert "尚無留言" in resp.text

    def test_renders_post_with_nested_replies(
        self, client: TestClient, storage: HorusStorage
    ) -> None:
        root = ScrapedItem(
            id="p1",
            site_id="threads",
            url="https://threads.net/t/p1",
            author_name="alice",
            text="root content",
            timestamp=datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
        )
        reply1 = ScrapedItem(
            id="r1",
            site_id="threads",
            url="https://threads.net/t/r1",
            author_name="bob",
            text="first reply",
            timestamp=datetime(2026, 1, 1, 14, 5, tzinfo=UTC),
            extra={"is_reply": True, "parent_post_id": "p1", "conversation_id": "p1"},
        )
        reply2 = ScrapedItem(
            id="r2",
            site_id="threads",
            url="https://threads.net/t/r2",
            author_name="carol",
            text="nested reply",
            timestamp=datetime(2026, 1, 1, 14, 10, tzinfo=UTC),
            extra={
                "is_reply": True,
                "parent_post_id": "r1",
                "conversation_id": "p1",
                "reply_to_username": "bob",
            },
        )
        storage.upsert_items([root, reply1, reply2])
        resp = client.get("/items/threads/p1")
        assert resp.status_code == 200
        assert "root content" in resp.text
        assert "first reply" in resp.text
        assert "nested reply" in resp.text
        assert "@bob" in resp.text
        assert "回覆 @bob" in resp.text


class TestDeleteItem:
    def test_delete_existing(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items([_make_item("del1", "to be deleted")])
        resp = client.delete("/items/threads/del1")
        assert resp.status_code == 200
        # Verify gone
        items = storage.get_items(site_id="threads")
        assert len(items) == 0

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/items/threads/nonexistent")
        assert resp.status_code == 404
