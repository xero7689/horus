"""Tests for /crawl routes — covers start, get, cancel, and error cases."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from horus.serve.crawler_manager import CrawlJob


class TestStartCrawl:
    def test_submit_returns_job_id(self, client: TestClient, mock_manager: MagicMock) -> None:
        mock_job = MagicMock(spec=CrawlJob)
        mock_job.job_id = "abc123"
        mock_manager.submit.return_value = mock_job

        resp = client.post("/crawl", json={"site": "threads", "kwargs": {"user": "@test"}})
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "abc123"

    def test_invalid_site_returns_400(self, client: TestClient, mock_manager: MagicMock) -> None:
        mock_manager.submit.side_effect = ValueError("Unknown site: nope")
        resp = client.post("/crawl", json={"site": "nope"})
        assert resp.status_code == 400
        assert "Unknown site" in resp.json()["detail"]


class TestGetJob:
    def test_existing_job(self, client: TestClient, mock_manager: MagicMock) -> None:
        mock_job = MagicMock(spec=CrawlJob)
        mock_job.to_dict.return_value = {"job_id": "j1", "status": "running"}
        mock_manager.get_job.return_value = mock_job

        resp = client.get("/crawl/j1")
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "j1"

    def test_nonexistent_returns_404(self, client: TestClient, mock_manager: MagicMock) -> None:
        mock_manager.get_job.return_value = None
        resp = client.get("/crawl/nonexistent")
        assert resp.status_code == 404


class TestCancelJob:
    def test_cancel_running_job(self, client: TestClient, mock_manager: MagicMock) -> None:
        mock_manager.cancel.return_value = True
        resp = client.delete("/crawl/j1")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

    def test_cancel_nonexistent_returns_404(
        self, client: TestClient, mock_manager: MagicMock
    ) -> None:
        mock_manager.cancel.return_value = False
        resp = client.delete("/crawl/nonexistent")
        assert resp.status_code == 404
