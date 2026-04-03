"""Tests for /stats and /adapters routes."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


class TestGetStats:
    def test_empty_db(self, client: TestClient) -> None:
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_with_items(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items(
            [
                ScrapedItem(
                    id="s1",
                    site_id="threads",
                    url="https://threads.net/t/s1",
                    author_name="user1",
                    text="hello",
                    timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        resp = client.get("/stats")
        data = resp.json()
        assert data["total"] == 1

    def test_filter_by_site(self, client: TestClient, storage: HorusStorage) -> None:
        storage.upsert_items(
            [
                ScrapedItem(
                    id="s1",
                    site_id="threads",
                    url="https://threads.net/t/s1",
                    author_name="user1",
                    text="hello",
                    timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        resp = client.get("/stats?site=threads")
        data = resp.json()
        assert data["total"] == 1

        resp = client.get("/stats?site=twitter")
        data = resp.json()
        assert data["total"] == 0


class TestGetAdapters:
    def test_returns_list(self, client: TestClient) -> None:
        resp = client.get("/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # Each adapter should have these fields
        adapter = data[0]
        assert "site_id" in adapter
        assert "display_name" in adapter
        assert "requires_login" in adapter
