"""Background crawl job manager for horus serve."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from horus.adapters import get_adapter
from horus.config import Settings
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


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
        started_at = datetime.now(UTC)

        try:
            adapter_cls = get_adapter(job.site)
            adapter = adapter_cls()

            scraper_kwargs: dict[str, Any] = {
                "headless": self._settings.headless,
                "scroll_delay_min": self._settings.scroll_delay_min,
                "scroll_delay_max": self._settings.scroll_delay_max,
                "request_jitter": self._settings.request_jitter,
                "max_pages": self._settings.max_pages,
            }

            if adapter_cls.has_http_mode:
                # Direct HTTP path (e.g. DDG)
                msg = f"Fetching via HTTP: {job.site}"
                job.log.append(msg)
                job.broadcast("log", {"message": msg})

                items = await adapter.fetch_items(**job.kwargs)
                items = adapter.post_process(items)
                new_count = storage.upsert_items(items)
                job.items_found = len(items)
                job.items_new = new_count
                storage.log_crawl(
                    job.site, str(job.kwargs), job.items_found, job.items_new, started_at
                )

            elif adapter_cls.has_page_mode:
                # Page-mode path (HTML → Markdown)
                urls = adapter.get_urls(**job.kwargs)
                async with BaseScraper(**scraper_kwargs) as scraper:
                    _sp = self._settings.state_path_for(job.site)
                    state_path = _sp if _sp.exists() else None
                    for url in urls:
                        msg = f"Fetching page: {url}"
                        job.log.append(msg)
                        job.broadcast("log", {"message": msg})
                        page = await scraper.scrape_page(url, state_path, site_id=job.site)
                        is_new = storage.upsert_page(page)
                        job.items_found += 1
                        job.items_new += int(is_new)
                        job.broadcast(
                            "progress", {"fetched": job.items_found, "new": job.items_new}
                        )
                storage.log_crawl(
                    job.site,
                    urls[0] if len(urls) == 1 else f"{len(urls)} URLs",
                    job.items_found,
                    job.items_new,
                    started_at,
                )

            else:
                # Response-intercept path (Threads etc.)
                urls = adapter.get_urls(**job.kwargs)
                since_dt = None
                if since:
                    from datetime import datetime as dt

                    try:
                        since_dt = dt.fromisoformat(since).replace(tzinfo=UTC)
                    except ValueError:
                        pass
                if since_dt is None:
                    since_dt = storage.get_latest_timestamp(job.site)

                raw_path = self._settings.state_path_for(job.site)
                state_path = raw_path if raw_path.exists() else None

                async with BaseScraper(**scraper_kwargs) as scraper:
                    for url in urls:
                        msg = f"Crawling: {url}"
                        job.log.append(msg)
                        job.broadcast("log", {"message": msg})

                        def on_progress(scroll: int, total: int) -> None:
                            job.items_found = total
                            job.broadcast("progress", {"scroll": scroll, "total": total})

                        def on_batch(batch: list[ScrapedItem]) -> None:
                            batch = adapter.post_process(batch)
                            new_count = storage.upsert_items(batch)
                            job.items_new += new_count

                        await scraper.scrape(
                            url,
                            adapter.get_response_filter(),
                            adapter.parse_response,
                            state_path,
                            since=since_dt,
                            on_progress=on_progress,
                            on_batch=on_batch,
                        )
                        storage.log_crawl(job.site, url, job.items_found, job.items_new, started_at)

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
