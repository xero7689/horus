"""Background crawl job manager for horus serve."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from rich.text import Text

from horus.adapters import get_adapter
from horus.config import Settings
from horus.core.dates import parse_since
from horus.core.orchestrator import CrawlOrchestrator
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CrawlJob:
    job_id: str
    site: str
    kwargs: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    items_found: int = 0
    items_new: int = 0
    error: str | None = None
    log: list[str] = field(default_factory=list)

    # Internal: SSE subscribers
    _subscribers: list[asyncio.Queue[str]] = field(default_factory=list, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def broadcast(self, event: str, data: dict[str, Any]) -> None:
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in self._subscribers:
            q.put_nowait(msg)

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


class _JobReporter:
    """Bridges CrawlReporter events onto a CrawlJob (SSE + log)."""

    def __init__(self, job: CrawlJob) -> None:
        self._job = job

    def log(self, message: str) -> None:
        # Strip Rich markup for the web log (serve renders plain text). Use Rich's
        # own parser — a hand-rolled regex misses styles with punctuation/attributes.
        # Messages interpolate unsanitized content (URLs, exception text): a stray
        # "[/...]" would raise MarkupError and fail the whole job, so fall back to
        # the raw string rather than let a log line kill the crawl.
        try:
            plain = Text.from_markup(message).plain
        except Exception:
            plain = message
        self._job.log.append(plain)
        self._job.broadcast("log", {"message": plain})

    def progress(self, scroll: int, total: int) -> None:
        self._job.broadcast("progress", {"scroll": scroll, "total": total})

    def emit(self, item: Any) -> None: ...  # serve does not stream items


class CrawlerManager:
    """Manages background crawl jobs. One instance per server lifetime."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jobs: dict[str, CrawlJob] = {}

    def list_jobs(self) -> list[CrawlJob]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> CrawlJob | None:
        return self._jobs.get(job_id)

    def submit(
        self,
        site: str,
        kwargs: dict[str, Any],
        limit: int = 50,
        since: str | None = None,
    ) -> CrawlJob:
        job_id = uuid.uuid4().hex[:8]
        job = CrawlJob(job_id=job_id, site=site, kwargs=kwargs)
        self._jobs[job_id] = job
        job._task = asyncio.create_task(
            self._run(job, limit, since),
            name=f"crawl-{job_id}",
        )
        return job

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job._task and not job._task.done():
            return job._task.cancel()
        return False

    async def _run(self, job: CrawlJob, limit: int, since: str | None) -> None:
        job.status = JobStatus.RUNNING
        job.broadcast("status", {"status": "running"})

        storage = HorusStorage(self._settings.resolved_db_path)

        try:
            adapter_cls = get_adapter(job.site)
            adapter = adapter_cls()

            # http-mode adapters may not implement get_urls (the http path never
            # uses URLs) — do not call it for them.
            urls: list[str] = [] if adapter_cls.has_http_mode else adapter.get_urls(**job.kwargs)

            since_dt = None
            if since:
                try:
                    since_dt = parse_since(since)
                except ValueError:
                    pass  # serve keeps silent-fallback semantics (documented divergence)

            raw_path = self._settings.state_path_for(job.site)
            state_path = raw_path if raw_path.exists() else None

            orchestrator = CrawlOrchestrator(
                storage,
                self._settings,
                scraper_cls=BaseScraper,  # module-level name: patchable in tests like cli
                reporter=_JobReporter(job),
            )
            outcome = await orchestrator.run(
                adapter,
                urls,
                adapter_cls=adapter_cls,
                site=job.site,
                kwargs=job.kwargs,
                limit=limit,
                since=since_dt,
                state_path=state_path,
            )
            job.items_found = outcome.total_found
            job.items_new = outcome.total_new

            job.status = JobStatus.DONE
            job.broadcast("done", {"items_found": job.items_found, "items_new": job.items_new})

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            job.broadcast("status", {"status": "cancelled"})
            raise
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.broadcast("error", {"message": str(e)})
        finally:
            job.finished_at = datetime.now(UTC)
            storage.close()
