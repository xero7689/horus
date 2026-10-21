"""Model → JSON-safe dicts for MCP tool results."""

from typing import Any

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem, ScrapedPage

TEXT_PREVIEW_CHARS = 500

# Adapter kwargs the `crawl` tool exposes as parameters. --url-list (a local
# file path) is deliberately absent.
EXPOSED_CRAWL_PARAMS = frozenset({"user", "url", "query", "mode", "with_comments", "gl", "hl"})


def _item_base(item: ScrapedItem) -> dict[str, Any]:
    return {
        "site_id": item.site_id,
        "id": item.id,
        "url": item.url,
        "author_name": item.author_name,
        "timestamp": item.timestamp.isoformat(),
        "extra": item.extra,
    }


def item_summary(item: ScrapedItem) -> dict[str, Any]:
    """List view: text cut to TEXT_PREVIEW_CHARS; use get_item for the rest."""
    text = item.text or ""
    return {
        **_item_base(item),
        "text": text[:TEXT_PREVIEW_CHARS],
        "text_truncated": len(text) > TEXT_PREVIEW_CHARS,
    }


def item_full(item: ScrapedItem) -> dict[str, Any]:
    return {**_item_base(item), "text": item.text or ""}


def page_summary(page: ScrapedPage) -> dict[str, Any]:
    return {"url": page.url, "title": page.title, "fetched_at": page.fetched_at.isoformat()}


def page_full(page: ScrapedPage, *, max_chars: int) -> dict[str, Any]:
    return {
        **page_summary(page),
        "markdown": page.markdown[:max_chars],
        "truncated": len(page.markdown) > max_chars,
    }


def option_to_param(cli_name: str) -> str:
    """`--with-comments` → `with_comments`."""
    return cli_name.lstrip("-").replace("-", "_")


def crawl_params(adapter: SiteAdapter) -> list[dict[str, Any]]:
    """The adapter's crawl options, renamed to MCP parameter names."""
    params = []
    for opt in adapter.get_crawl_options():
        name = option_to_param(opt["name"])
        if name in EXPOSED_CRAWL_PARAMS:
            params.append({**opt, "name": name})
    return params
