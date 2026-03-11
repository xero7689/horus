from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from horus.adapters import list_adapters
from horus.core.storage import HorusStorage
from horus.serve.deps import get_storage, get_templates

router = APIRouter(prefix="/pages")


def _site_ids() -> list[str]:
    return sorted(cls.site_id for cls in list_adapters())


@router.get("", response_class=HTMLResponse)
async def list_pages(
    request: Request,
    site: str | None = None,
    limit: int = 50,
    storage: HorusStorage = Depends(get_storage),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    page_list = storage.get_pages(site_id=site, limit=limit)

    is_htmx = request.headers.get("HX-Request") == "true"
    template = "partials/page_list.html" if is_htmx else "pages.html"
    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "pages": page_list,
            "site": site,
            "site_ids": _site_ids(),
            "limit": limit,
            "active": "pages",
        },
    )


@router.delete("")
async def delete_page(
    url: str = Query(...),
    storage: HorusStorage = Depends(get_storage),
) -> Response:
    deleted = storage.delete_page(url)
    if not deleted:
        raise HTTPException(status_code=404, detail="Page not found")
    return Response(status_code=200)
