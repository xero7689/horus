"""FastAPI application for horus serve."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from horus.config import Settings
from horus.serve import deps
from horus.serve.crawler_manager import CrawlerManager
from horus.serve.routes import crawl, items, pages, stats

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = Settings()
    settings.ensure_dirs()
    manager = CrawlerManager(settings)
    deps.init(settings, manager, _TEMPLATES_DIR)
    yield
    # Cancel all running jobs on shutdown
    for job in manager.list_jobs():
        manager.cancel(job.job_id)


def create_app() -> FastAPI:
    app = FastAPI(title="Horus", lifespan=lifespan)
    app.include_router(stats.router)
    app.include_router(items.router)
    app.include_router(pages.router)
    app.include_router(crawl.router)
    return app


app = create_app()
