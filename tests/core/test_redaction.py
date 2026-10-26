"""Tests for kwargs secret redaction.

Covers the helper itself plus the two exposure points it guards: the http-mode
`crawl_log` row and the background job's outward serialization (serve job API).
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.crawl_plan import CrawlPlan
from horus.core.jobs import CrawlJob, JobManager
from horus.core.orchestrator import CrawlOrchestrator
from horus.core.redaction import MASK, mask_secrets
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


def test_masks_known_secret_keys() -> None:
    masked = mask_secrets(
        {
            "api_key": "sk-live-1",
            "apikey": "sk-live-2",
            "token": "t",
            "secret": "s",
            "password": "p",
        }
    )
    assert set(masked.values()) == {MASK}


def test_masks_case_insensitively_and_across_separators() -> None:
    masked = mask_secrets({"API_KEY": "a", "apiKey": "b", "api-key": "c", "Access-Token": "d"})
    assert set(masked.values()) == {MASK}


def test_masks_keys_that_merely_contain_a_hint() -> None:
    masked = mask_secrets({"serper_api_key": "a", "refresh_token_value": "b"})
    assert set(masked.values()) == {MASK}


def test_leaves_other_fields_untouched() -> None:
    kwargs = {"query": "fastapi tutorial", "gl": "tw", "limit": 10, "with_comments": "true"}
    assert mask_secrets(kwargs) == kwargs


def test_does_not_mutate_the_input() -> None:
    kwargs = {"api_key": "sk-live", "query": "x"}
    masked = mask_secrets(kwargs)
    assert kwargs["api_key"] == "sk-live"
    assert masked is not kwargs
    assert masked["api_key"] == MASK


def test_empty_kwargs() -> None:
    assert mask_secrets({}) == {}


def test_masks_nested_mappings() -> None:
    # serve's POST /crawl accepts arbitrary JSON values, not just flat strings.
    kwargs = {"query": "x", "auth": {"token": "t", "user": "u"}}
    assert mask_secrets(kwargs) == {"query": "x", "auth": {"token": MASK, "user": "u"}}
    assert kwargs["auth"]["token"] == "t"


class _HttpStub(SiteAdapter):
    site_id = "redact_stub"
    display_name = "Redact Stub"
    login_url = ""
    requires_login = False
    has_http_mode = True

    received_kwargs: dict[str, Any] = {}

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        type(self).received_kwargs = kwargs
        return [
            ScrapedItem(
                id="R1",
                site_id="redact_stub",
                url="https://e.com/1",
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            )
        ]


def test_http_mode_crawl_log_is_masked_but_adapter_gets_the_real_value(tmp_path: Any) -> None:
    _HttpStub.received_kwargs = {}
    storage = HorusStorage(tmp_path / "t.db")
    orch = CrawlOrchestrator(storage, Settings())
    asyncio.run(
        orch.run(
            _HttpStub(),
            [],
            site="redact_stub",
            kwargs={"query": "x", "api_key": "sk-live-SECRET"},
            limit=10,
            since=None,
            state_path=None,
        )
    )

    logged = storage._conn.execute(
        "SELECT url FROM crawl_log WHERE site_id = ?", ("redact_stub",)
    ).fetchone()["url"]
    assert "sk-live-SECRET" not in logged
    assert MASK in logged
    assert "x" in logged  # non-secret kwargs still recorded

    # The adapter must still receive the true value (redaction is log-only).
    assert _HttpStub.received_kwargs["api_key"] == "sk-live-SECRET"
    storage.close()


def test_job_serialization_is_masked() -> None:
    job = CrawlJob(job_id="j1", site="serper", kwargs={"query": "x", "token": "SECRET"})
    assert job.to_dict()["kwargs"] == {"query": "x", "token": MASK}
    # In-memory job state keeps the real value for the crawl itself.
    assert job.kwargs["token"] == "SECRET"


def test_background_job_crawl_log_is_masked(tmp_path: Any) -> None:
    """End-to-end through JobManager: the serve and mcp paths share the same guard."""
    _HttpStub.received_kwargs = {}
    db = tmp_path / "jobs.db"
    plan = CrawlPlan(
        site="redact_stub",
        adapter_cls=_HttpStub,
        adapter=_HttpStub(),
        urls=[],
        state_path=None,
        login_state_missing=False,
    )

    async def run() -> CrawlJob:
        mgr = JobManager(Settings(base_dir=tmp_path), storage_factory=lambda: HorusStorage(db))
        job = mgr.submit(plan, {"query": "x", "api_key": "SECRET"}, limit=10, since=None)
        assert job.task is not None
        await job.task
        return job

    job = asyncio.run(run())
    assert job.error is None, job.error
    assert _HttpStub.received_kwargs["api_key"] == "SECRET"

    # `_run` closes its storage in a finally block; reopen the file to read the row.
    reopened = HorusStorage(db)
    logged = reopened._conn.execute(
        "SELECT url FROM crawl_log WHERE site_id = ?", ("redact_stub",)
    ).fetchone()["url"]
    assert "SECRET" not in logged
    assert MASK in logged
    reopened.close()
