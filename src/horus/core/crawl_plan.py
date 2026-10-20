"""Shared crawl prelude: resolve adapter, URLs and login state for one crawl.

Every crawl frontend should start a crawl through this (cli does today); each
frontend only decides how to surface the ValueError and what to do about a
missing login state.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from horus.adapters import get_adapter
from horus.adapters.base import SiteAdapter
from horus.config import Settings


@dataclass(frozen=True)
class CrawlPlan:
    site: str
    adapter_cls: type[SiteAdapter]
    adapter: SiteAdapter
    urls: list[str]
    state_path: Path | None
    login_state_missing: bool


def resolve_crawl_plan(
    site: str,
    kwargs: dict[str, Any],
    settings: Settings,
    *,
    use_saved_state: bool = True,
) -> CrawlPlan:
    """Resolve everything a crawl needs before it starts.

    Raises:
        ValueError: unknown site, or the adapter rejected `kwargs` (missing
            --user / --url / ...). Messages are user-facing as-is.
    """
    adapter_cls = get_adapter(site)
    adapter = adapter_cls()
    # http-mode adapters may not implement get_urls meaningfully (the http path
    # never uses URLs) and read their query inside fetch_items — skip it.
    urls: list[str] = [] if adapter_cls.has_http_mode else adapter.get_urls(**kwargs)

    state_path: Path | None = None
    login_state_missing = False
    if use_saved_state:
        candidate = settings.state_path_for(site)
        if candidate.exists():
            state_path = candidate
        elif adapter_cls.requires_login:
            login_state_missing = True

    return CrawlPlan(
        site=site,
        adapter_cls=adapter_cls,
        adapter=adapter,
        urls=urls,
        state_path=state_path,
        login_state_missing=login_state_missing,
    )
