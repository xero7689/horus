"""CrawlerManager-level test: http-mode forwards the serve `limit` into fetch_items.

The serve UI's crawl form has its own `limit` field which reaches
`CrawlerManager._run(job, limit, since)` as the `limit` parameter. The http-mode
branch must forward it into `adapter.fetch_items`, mirroring the CLI fix — kwargs
wins on collision.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from horus.core.storage import HorusStorage
from horus.models import ScrapedItem
from horus.serve.crawler_manager import CrawlerManager, CrawlJob


class _FakeHttpAdapter:
    """Minimal http-mode adapter that records the kwargs fetch_items received."""

    site_id = "fake-http"
    has_http_mode = True
    has_page_mode = False
    received_kwargs: dict[str, Any] = {}

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        type(self).received_kwargs = kwargs
        return []

    def post_process(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        return items


@pytest.mark.asyncio
async def test_http_mode_forwards_limit_into_fetch_items(monkeypatch) -> None:
    _FakeHttpAdapter.received_kwargs = {}

    monkeypatch.setattr(
        "horus.serve.crawler_manager.get_adapter",
        lambda _site: _FakeHttpAdapter,
    )

    settings = MagicMock(resolved_db_path=Path(":memory:"))
    manager = CrawlerManager(settings)

    # Use a real in-memory storage so upsert_items / log_crawl work end-to-end.
    storage = HorusStorage(Path(":memory:"), check_same_thread=False)
    monkeypatch.setattr(
        "horus.serve.crawler_manager.HorusStorage",
        lambda _path: storage,
    )

    job = CrawlJob(job_id="t1", site="fake-http", kwargs={"query": "x"})
    await manager._run(job, limit=42, since=None)

    assert _FakeHttpAdapter.received_kwargs.get("limit") == 42
    assert _FakeHttpAdapter.received_kwargs.get("query") == "x"


@pytest.mark.asyncio
async def test_http_mode_job_kwargs_win_on_limit_collision(monkeypatch) -> None:
    """If job.kwargs already carries `limit`, it takes precedence over the serve limit."""
    _FakeHttpAdapter.received_kwargs = {}

    monkeypatch.setattr(
        "horus.serve.crawler_manager.get_adapter",
        lambda _site: _FakeHttpAdapter,
    )
    storage = HorusStorage(Path(":memory:"), check_same_thread=False)
    monkeypatch.setattr(
        "horus.serve.crawler_manager.HorusStorage",
        lambda _path: storage,
    )

    manager = CrawlerManager(MagicMock(resolved_db_path=Path(":memory:")))
    job = CrawlJob(job_id="t2", site="fake-http", kwargs={"query": "x", "limit": 7})
    await manager._run(job, limit=42, since=None)

    assert _FakeHttpAdapter.received_kwargs.get("limit") == 7


@pytest.mark.asyncio
async def test_submit_normalizes_option_keys_to_adapter_kwargs(monkeypatch) -> None:
    """Form option names ("some-key", "--x-y") reach fetch_items as snake_case."""
    _FakeHttpAdapter.received_kwargs = {}
    monkeypatch.setattr("horus.serve.crawler_manager.get_adapter", lambda _site: _FakeHttpAdapter)
    storage = HorusStorage(Path(":memory:"), check_same_thread=False)
    monkeypatch.setattr("horus.serve.crawler_manager.HorusStorage", lambda _path: storage)

    manager = CrawlerManager(MagicMock(resolved_db_path=Path(":memory:")))
    job = manager.submit("fake-http", {"query": "x", "some-key": "v", "--url-list": "f"})
    assert job._task is not None
    await job._task

    assert _FakeHttpAdapter.received_kwargs.get("some_key") == "v"
    assert _FakeHttpAdapter.received_kwargs.get("url_list") == "f"
    assert "some-key" not in _FakeHttpAdapter.received_kwargs
