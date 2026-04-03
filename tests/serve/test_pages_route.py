"""Tests for /pages routes — covers delete and 404."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from horus.core.storage import HorusStorage
from horus.models import ScrapedPage


class TestDeletePage:
    def test_delete_existing(self, client: TestClient, storage: HorusStorage) -> None:
        page = ScrapedPage(
            url="https://example.com/test",
            site_id="web",
            title="Test Page",
            markdown="some content",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        storage.upsert_page(page)
        resp = client.delete("/pages", params={"url": "https://example.com/test"})
        assert resp.status_code == 200

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/pages", params={"url": "https://example.com/nope"})
        assert resp.status_code == 404
