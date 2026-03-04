from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from horus.adapters import list_adapters
from horus.core.storage import HorusStorage
from horus.serve.deps import get_storage, get_templates

router = APIRouter()


@router.get("/stats")
def get_stats(
    site: str | None = None,
    storage: HorusStorage = Depends(get_storage),
) -> dict[str, Any]:
    return storage.get_stats(site_id=site)


@router.get("/adapters")
def get_adapter_list() -> list[dict[str, Any]]:
    return [
        {
            "site_id": cls.site_id,
            "display_name": cls.display_name,
            "requires_login": cls.requires_login,
            "description": cls.description,
            "crawl_options": cls().get_crawl_options(),
        }
        for cls in list_adapters()
    ]


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    storage: HorusStorage = Depends(get_storage),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    stats = storage.get_stats()
    adapters = list_adapters()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "stats": stats, "adapters": adapters, "active": "home"},
    )
