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
