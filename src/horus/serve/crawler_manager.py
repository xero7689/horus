"""Background crawl job manager for horus serve: JobManager + SSE fan-out."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from horus.config import Settings
from horus.core.crawl_plan import resolve_crawl_plan
from horus.core.dates import parse_since
from horus.core.jobs import CrawlJob, JobManager, JobStatus
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage

__all__ = ["CrawlJob", "CrawlerManager", "JobStatus"]


class CrawlerManager:
    """Manages background crawl jobs. One instance per server lifetime."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}
        self._jobs = JobManager(
            settings,
            storage_factory=lambda: HorusStorage(settings.resolved_db_path),
            scraper_cls=BaseScraper,  # module-level name: patchable in tests like cli
            on_event=self._broadcast,
        )

    def list_jobs(self) -> list[CrawlJob]:
        return self._jobs.list_jobs()

    def get_job(self, job_id: str) -> CrawlJob | None:
        return self._jobs.get(job_id)

    def submit(
        self,
        site: str,
        kwargs: dict[str, Any],
        limit: int = 50,
        since: str | None = None,
    ) -> CrawlJob:
        """Start a crawl.

        Raises:
            ValueError: unknown site or rejected kwargs (routes map it to 400).
        """
        # The crawl form posts option names like "with-comments"; adapters and the
        # orchestrator read "with_comments" (CLI-style normalization).
        normalized = {k.lstrip("-").replace("-", "_"): v for k, v in kwargs.items()}
        since_dt = None
        if since:
            try:
                since_dt = parse_since(since)
            except ValueError:
                pass  # serve keeps silent-fallback semantics (documented divergence)
        plan = resolve_crawl_plan(site, normalized, self._settings)
        return self._jobs.submit(plan, normalized, limit=limit, since=since_dt)

    def cancel(self, job_id: str) -> bool:
        return self._jobs.cancel(job_id)

    def subscribe(self, job_id: str) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[str]) -> None:
        queues = self._subscribers.get(job_id, [])
        try:
            queues.remove(q)
        except ValueError:
            pass
        if not queues:
            self._subscribers.pop(job_id, None)

    def _broadcast(self, job: CrawlJob, event: str, data: dict[str, Any]) -> None:
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in list(self._subscribers.get(job.job_id, [])):
            q.put_nowait(msg)
