from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from horus.adapters import list_adapters
from horus.core.storage import HorusStorage
from horus.serve.deps import get_storage, get_templates

router = APIRouter(prefix="/items")


def _site_ids() -> list[str]:
    return sorted(cls.site_id for cls in list_adapters())


@router.get("", response_class=HTMLResponse)
async def list_items(
    request: Request,
    site: str | None = None,
    author: str | None = None,
    since: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    storage: HorusStorage = Depends(get_storage),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=UTC)
        except ValueError:
            pass

    if q:
        items = storage.search(q, site_id=site, limit=limit)
    else:
        items = storage.get_items(site_id=site, author_name=author, since=since_dt, limit=limit)

    is_htmx = request.headers.get("HX-Request") == "true"
    template = "partials/item_list.html" if is_htmx else "items.html"
    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "items": items,
            "site": site,
            "author": author,
            "since": since,
            "q": q,
            "limit": limit,
            "offset": offset,
            "site_ids": _site_ids(),
            "active": "items",
        },
    )


@router.delete("/{site_id}/{item_id}")
async def delete_item(
    site_id: str,
    item_id: str,
    storage: HorusStorage = Depends(get_storage),
) -> Response:
    deleted = storage.delete_item(site_id, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found")
    return Response(status_code=200)
