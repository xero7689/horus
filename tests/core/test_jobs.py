"""core.jobs: UI-agnostic background crawl jobs shared by serve and mcp."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.crawl_plan import CrawlPlan
from horus.core.jobs import CrawlJob, JobManager, JobStatus, strip_markup
from horus.core.orchestrator import CrawlOutcome
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


def test_strip_markup_removes_rich_tags() -> None:
    assert strip_markup("Crawling [cyan]https://e.com[/cyan]...") == "Crawling https://e.com..."


def test_strip_markup_falls_back_on_broken_markup() -> None:
    raw = "bad [/oops] closing tag"
    assert strip_markup(raw) == raw


def test_counts_read_live_from_outcome() -> None:
    job = CrawlJob(job_id="j1", site="web", kwargs={"url": "https://e.com"})
    job.outcome.total_found = 3
    job.outcome.total_new = 2
    assert (job.items_found, job.items_new) == (3, 2)


def test_to_dict_matches_serve_fields() -> None:
    job = CrawlJob(job_id="j1", site="web", kwargs={"url": "u"}, outcome=CrawlOutcome(5, 4))
    job.log.extend(f"line {i}" for i in range(25))
    d = job.to_dict()
    assert set(d) == {
        "job_id",
        "site",
        "kwargs",
        "status",
        "started_at",
        "finished_at",
        "items_found",
        "items_new",
        "error",
        "log",
    }
    assert d["status"] == JobStatus.PENDING
    assert d["items_found"] == 5 and d["items_new"] == 4
    assert d["log"] == [f"line {i}" for i in range(5, 25)]


def test_finished_flag() -> None:
    job = CrawlJob(job_id="j1", site="web", kwargs={})
    assert job.finished is False
    for status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
        job.status = status
        assert job.finished is True


class _HttpStub(SiteAdapter):
    site_id = "jobs_http"
    display_name = "Jobs Http"
    login_url = ""
    requires_login = False
    has_http_mode = True

    gate: asyncio.Event | None = None
    fail_with: Exception | None = None

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        if type(self).gate is not None:
            await type(self).gate.wait()
        if type(self).fail_with is not None:
            raise type(self).fail_with
        return [
            ScrapedItem(
                id=f"x{i}",
                site_id="jobs_http",
                url=f"https://e.com/{i}",
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            )
            for i in range(25)
        ]


@pytest.fixture(autouse=True)
def _reset_stub() -> None:
    _HttpStub.gate = None
    _HttpStub.fail_with = None


def _plan() -> CrawlPlan:
    return CrawlPlan(
        site="jobs_http",
        adapter_cls=_HttpStub,
        adapter=_HttpStub(),
        urls=[],
        state_path=None,
        login_state_missing=False,
    )


def _manager(tmp_path: Path, **kw: Any) -> JobManager:
    db = tmp_path / "t.db"
    return JobManager(
        Settings(base_dir=tmp_path, _env_file=None),
        storage_factory=lambda: HorusStorage(db),
        **kw,
    )


async def test_done_job_has_counts_and_capped_refs(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
    assert job.task is not None
    await job.task
    assert job.status == JobStatus.DONE
    assert job.items_found == 25 and job.items_new == 25
    assert job.refs == [f"x{i}" for i in range(20)]
    assert job.finished_at is not None


async def test_failed_job_records_error_and_type(tmp_path: Path) -> None:
    _HttpStub.fail_with = RuntimeError("boom")
    mgr = _manager(tmp_path)
    job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
    assert job.task is not None
    await job.task
    assert job.status == JobStatus.FAILED
    assert (job.error, job.error_type) == ("boom", "RuntimeError")


async def test_cancel_running_job(tmp_path: Path) -> None:
    _HttpStub.gate = asyncio.Event()
    mgr = _manager(tmp_path)
    job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
    await asyncio.sleep(0)  # let it start
    assert mgr.cancel(job.job_id) is True
    with pytest.raises(asyncio.CancelledError):
        await job.task  # type: ignore[misc]
    assert job.status == JobStatus.CANCELLED
    assert mgr.cancel(job.job_id) is False


async def test_cancel_before_start_marks_cancelled(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
    assert mgr.cancel(job.job_id) is True  # task created but never ran
    with pytest.raises(asyncio.CancelledError):
        await job.task  # type: ignore[misc]
    await asyncio.sleep(0)  # done callbacks run via call_soon
    assert job.status == JobStatus.CANCELLED
    assert job.finished_at is not None


async def test_events_match_serve_sse_shapes(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    mgr = _manager(tmp_path, on_event=lambda _job, name, data: events.append((name, data)))
    job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
    await job.task  # type: ignore[misc]
    names = [n for n, _ in events]
    assert names[0] == "status" and events[0][1] == {"status": "running"}
    assert "log" in names and all("message" in d for n, d in events if n == "log")
    assert events[-1] == ("done", {"items_found": 25, "items_new": 25})


async def test_max_finished_prunes_oldest(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, max_finished=2)
    jobs = []
    for _ in range(3):
        job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
        await job.task  # type: ignore[misc]
        await asyncio.sleep(0)  # done callbacks (prune) run via call_soon
        jobs.append(job)
    assert [j.job_id for j in mgr.list_jobs()] == [jobs[1].job_id, jobs[2].job_id]
    assert mgr.get(jobs[0].job_id) is None


async def test_max_finished_none_keeps_everything(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    for _ in range(3):
        job = mgr.submit(_plan(), {"query": "q"}, limit=10, since=None)
        await job.task  # type: ignore[misc]
    assert len(mgr.list_jobs()) == 3
