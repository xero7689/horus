import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from horus.adapters import list_adapters
from horus.serve.crawler_manager import CrawlerManager, JobStatus
from horus.serve.deps import get_manager, get_templates

router = APIRouter(prefix="/crawl")


class CrawlRequest(BaseModel):
    site: str
    kwargs: dict[str, Any] = {}
    limit: int = 50
    since: str | None = None


@router.get("", response_class=HTMLResponse)
async def crawl_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
    manager: CrawlerManager = Depends(get_manager),
) -> HTMLResponse:
    is_htmx = request.headers.get("HX-Request") == "true"
    jobs = [j.to_dict() for j in reversed(manager.list_jobs())]

    if is_htmx:
        return templates.TemplateResponse(
            "partials/job_list.html",
            {"request": request, "jobs": jobs},
        )

    adapters = [
        {
            "site_id": cls.site_id,
            "display_name": cls.display_name,
            "crawl_options": cls().get_crawl_options(),
        }
        for cls in list_adapters()
    ]
    return templates.TemplateResponse(
        "crawl.html",
        {"request": request, "jobs": jobs, "adapters": adapters, "active": "crawl"},
    )


@router.post("")
async def start_crawl(
    body: CrawlRequest,
    manager: CrawlerManager = Depends(get_manager),
) -> dict[str, str]:
    try:
        job = manager.submit(body.site, body.kwargs, body.limit, body.since)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"job_id": job.job_id}


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    manager: CrawlerManager = Depends(get_manager),
) -> dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


async def _job_event_stream(job_id: str, manager: CrawlerManager) -> AsyncGenerator[str]:
    job = manager.get_job(job_id)
    if not job:
        yield f"event: error\ndata: {json.dumps({'message': 'Job not found'})}\n\n"
        return

    if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
        event = "done" if job.status == JobStatus.DONE else "error"
        data: dict[str, Any] = (
            {"items_found": job.items_found, "items_new": job.items_new}
            if job.status == JobStatus.DONE
            else {"message": job.error or job.status.value}
        )
        yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
        return

    q = job.subscribe()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30)
                yield msg
                if any(f"event: {e}" in msg for e in ("done", "error")):
                    break
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        job.unsubscribe(q)


@router.get("/{job_id}/stream")
async def stream_job(
    job_id: str,
    manager: CrawlerManager = Depends(get_manager),
) -> StreamingResponse:
    return StreamingResponse(
        _job_event_stream(job_id, manager),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/{job_id}")
async def cancel_job(
    job_id: str,
    manager: CrawlerManager = Depends(get_manager),
) -> dict[str, bool]:
    cancelled = manager.cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"cancelled": True}
