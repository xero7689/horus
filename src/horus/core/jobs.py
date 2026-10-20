"""UI-agnostic background crawl jobs.

Holds lifecycle mechanics only. serve wraps this with SSE fan-out; frontend
policy (event fan-out, error formatting, concurrency rules) stays in the
frontend.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from rich.text import Text

from horus.config import Settings
from horus.core.crawl_plan import CrawlPlan
from horus.core.orchestrator import CrawlOrchestrator, CrawlOutcome
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem, ScrapedPage


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})


def strip_markup(message: str) -> str:
    """Rich markup → plain text for job logs.

    Messages interpolate unsanitized content (URLs, exception text): a stray
    "[/...]" raises MarkupError, so fall back to the raw string rather than let
    a log line kill the crawl.
    """
    try:
        return Text.from_markup(message).plain
    except Exception:
        return message


@dataclass
class CrawlJob:
    job_id: str
    site: str
    kwargs: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    # Passed into CrawlOrchestrator.run(outcome=...) so counts are live.
    outcome: CrawlOutcome = field(default_factory=CrawlOutcome)
    refs: list[str] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    log: list[str] = field(default_factory=list)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def items_found(self) -> int:
        return self.outcome.total_found

    @property
    def items_new(self) -> int:
        return self.outcome.total_new

    @property
    def finished(self) -> bool:
        return self.status in _TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "site": self.site,
            "kwargs": self.kwargs,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "items_found": self.items_found,
            "items_new": self.items_new,
            "error": self.error,
            "log": self.log[-20:],  # last 20 log lines
        }


JobEvent = Callable[[CrawlJob, str, dict[str, Any]], None]

_MAX_REFS = 20


class _JobReporter:
    """CrawlReporter that records onto a CrawlJob and forwards events."""

    def __init__(self, job: CrawlJob, notify: JobEvent) -> None:
        self._job = job
        self._notify = notify

    def log(self, message: str) -> None:
        plain = strip_markup(message)
        self._job.log.append(plain)
        self._notify(self._job, "log", {"message": plain})

    def progress(self, scroll: int, total: int) -> None:
        self._notify(self._job, "progress", {"scroll": scroll, "total": total})

    def emit(self, item: ScrapedItem | ScrapedPage) -> None:
        if len(self._job.refs) < _MAX_REFS:
            self._job.refs.append(item.id if isinstance(item, ScrapedItem) else item.url)


class JobManager:
    """Runs crawls as asyncio tasks. One instance per server lifetime."""

    def __init__(
        self,
        settings: Settings,
        *,
        storage_factory: Callable[[], HorusStorage],
        scraper_cls: type[BaseScraper] = BaseScraper,
        on_event: JobEvent | None = None,
        max_finished: int | None = None,
    ) -> None:
        self._settings = settings
        self._storage_factory = storage_factory
        self._scraper_cls = scraper_cls
        self._on_event = on_event
        self._max_finished = max_finished
        self._jobs: dict[str, CrawlJob] = {}

    def submit(
        self,
        plan: CrawlPlan,
        kwargs: dict[str, Any],
        *,
        limit: int,
        since: datetime | None,
    ) -> CrawlJob:
        job = CrawlJob(job_id=uuid.uuid4().hex[:8], site=plan.site, kwargs=kwargs)
        self._jobs[job.job_id] = job
        job.task = asyncio.create_task(
            self._run(job, plan, limit, since), name=f"crawl-{job.job_id}"
        )
        job.task.add_done_callback(lambda _t, j=job: self._on_task_done(j))
        return job

    def get(self, job_id: str) -> CrawlJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[CrawlJob]:
        return list(self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.task and not job.task.done():
            return job.task.cancel()
        return False

    def _notify(self, job: CrawlJob, event: str, data: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(job, event, data)

    async def _run(
        self, job: CrawlJob, plan: CrawlPlan, limit: int, since: datetime | None
    ) -> None:
        storage: HorusStorage | None = None
        try:
            job.status = JobStatus.RUNNING
            self._notify(job, "status", {"status": "running"})
            storage = self._storage_factory()
            orchestrator = CrawlOrchestrator(
                storage,
                self._settings,
                scraper_cls=self._scraper_cls,
                reporter=_JobReporter(job, self._notify),
            )
            await orchestrator.run(
                plan.adapter,
                plan.urls,
                adapter_cls=plan.adapter_cls,
                site=plan.site,
                kwargs=job.kwargs,
                limit=limit,
                since=since,
                state_path=plan.state_path,
                outcome=job.outcome,
            )
            job.status = JobStatus.DONE
            self._notify(job, "done", {"items_found": job.items_found, "items_new": job.items_new})
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            self._notify(job, "status", {"status": "cancelled"})
            raise
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.error_type = type(e).__name__
            self._notify(job, "error", {"message": str(e)})
        finally:
            job.finished_at = datetime.now(UTC)
            if storage is not None:
                storage.close()

    def _on_task_done(self, job: CrawlJob) -> None:
        # A task cancelled before its first step never enters _run.
        if not job.finished:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
        self._prune()

    def _prune(self) -> None:
        if self._max_finished is None:
            return
        finished = [j for j in self._jobs.values() if j.finished]
        for job in finished[: max(0, len(finished) - self._max_finished)]:
            del self._jobs[job.job_id]
