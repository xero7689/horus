"""CrawlerManager forwards the serve `limit` into the shared JobManager.

The serve UI's crawl form has its own `limit` field. It must reach http-mode
`adapter.fetch_items`, mirroring the CLI — kwargs wins on collision.
"""

from pathlib import Path
from typing import Any

import pytest

from horus.adapters import get_adapter
from horus.config import Settings
from horus.models import ScrapedItem
from horus.serve.crawler_manager import CrawlerManager, JobStatus


class _FakeHttpAdapter:
    """Minimal http-mode adapter that records the kwargs fetch_items received."""

    site_id = "fake-http"
    has_http_mode = True
    has_page_mode = False
    requires_login = False
    received_kwargs: dict[str, Any] = {}

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        type(self).received_kwargs = kwargs
        return []

    def post_process(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        return items


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CrawlerManager:
    _FakeHttpAdapter.received_kwargs = {}
    monkeypatch.setattr("horus.core.crawl_plan.get_adapter", lambda _site: _FakeHttpAdapter)
    # db_path explicit: a developer's exported HORUS_DB_PATH must not leak into tests.
    return CrawlerManager(Settings(base_dir=tmp_path, db_path=tmp_path / "t.db", _env_file=None))


async def test_http_mode_forwards_limit_into_fetch_items(manager: CrawlerManager) -> None:
    job = manager.submit("fake-http", {"query": "x"}, limit=42)
    await job.task  # type: ignore[misc]
    assert job.status == JobStatus.DONE
    assert _FakeHttpAdapter.received_kwargs.get("limit") == 42
    assert _FakeHttpAdapter.received_kwargs.get("query") == "x"


async def test_kwargs_limit_wins_on_collision(manager: CrawlerManager) -> None:
    job = manager.submit("fake-http", {"query": "x", "limit": 7}, limit=42)
    await job.task  # type: ignore[misc]
    assert _FakeHttpAdapter.received_kwargs.get("limit") == 7


async def test_submit_normalizes_option_keys_to_adapter_kwargs(manager: CrawlerManager) -> None:
    """Form option names ("some-key", "--x-y") reach fetch_items as snake_case."""
    job = manager.submit("fake-http", {"query": "x", "some-key": "v", "--url-list": "f"})
    await job.task  # type: ignore[misc]
    assert _FakeHttpAdapter.received_kwargs.get("some_key") == "v"
    assert _FakeHttpAdapter.received_kwargs.get("url_list") == "f"
    assert "some-key" not in _FakeHttpAdapter.received_kwargs


async def test_bad_since_is_silently_ignored(manager: CrawlerManager) -> None:
    job = manager.submit("fake-http", {"query": "x"}, limit=1, since="not-a-date")
    await job.task  # type: ignore[misc]
    assert job.status == JobStatus.DONE


async def test_subscribers_receive_sse_formatted_events(manager: CrawlerManager) -> None:
    job = manager.submit("fake-http", {"query": "x"}, limit=1)
    q = manager.subscribe(job.job_id)
    await job.task  # type: ignore[misc]
    messages = []
    while not q.empty():
        messages.append(q.get_nowait())
    manager.unsubscribe(job.job_id, q)
    assert any(m.startswith("event: done\n") for m in messages)


async def test_unknown_site_raises_value_error_and_leaves_no_job(
    monkeypatch: pytest.MonkeyPatch, manager: CrawlerManager
) -> None:
    monkeypatch.setattr("horus.core.crawl_plan.get_adapter", get_adapter)  # undo fixture patch
    with pytest.raises(ValueError):
        manager.submit("no-such-site", {})
    assert manager.list_jobs() == []


async def test_non_value_error_in_setup_becomes_value_error(
    monkeypatch: pytest.MonkeyPatch, manager: CrawlerManager
) -> None:
    class _BadUrlsAdapter(_FakeHttpAdapter):
        has_http_mode = False

        def get_urls(self, **kwargs: Any) -> list[str]:
            raise IsADirectoryError("x")

    monkeypatch.setattr("horus.core.crawl_plan.get_adapter", lambda _site: _BadUrlsAdapter)
    with pytest.raises(ValueError, match="IsADirectoryError"):
        manager.submit("fake-http", {"url_list": "/tmp"})
    assert manager.list_jobs() == []
