"""Per-process MCP server state: shared storage, job manager, MCP-only policy."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from horus.config import Settings
from horus.core.jobs import CrawlJob, JobManager
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage

MAX_FINISHED_JOBS = 50


@dataclass
class McpState:
    settings: Settings
    storage: HorusStorage
    jobs: JobManager

    def running_job_for(self, site: str) -> CrawlJob | None:
        """MCP-only policy: one crawl per site (same login state, two browsers
        looks like a bot). Serve does not apply this."""
        return next((j for j in self.jobs.list_jobs() if j.site == site and not j.finished), None)

    async def shutdown(self) -> None:
        tasks = [j.task for j in self.jobs.list_jobs() if j.task and not j.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.storage.close()


def create_state(
    settings: Settings,
    *,
    storage_factory: Callable[[], HorusStorage] | None = None,
    scraper_cls: type[BaseScraper] = BaseScraper,
) -> McpState:
    factory = storage_factory or (lambda: HorusStorage(settings.resolved_db_path))
    return McpState(
        settings=settings,
        storage=factory(),
        jobs=JobManager(
            settings,
            storage_factory=factory,
            scraper_cls=scraper_cls,
            max_finished=MAX_FINISHED_JOBS,
        ),
    )


def format_job_error(job: CrawlJob) -> str | None:
    if job.error is None:
        return None
    return f"{job.error_type}: {job.error}" if job.error_type else job.error


def job_payload(job: CrawlJob) -> dict[str, Any]:
    return {
        "status": job.status.value,
        "job_id": job.job_id,
        "site": job.site,
        "items_found": job.items_found,
        "items_new": job.items_new,
        "refs": list(job.refs),
        "log": job.log[-20:],
        "error": format_job_error(job),
    }
