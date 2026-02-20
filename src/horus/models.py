from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ScrapedItem(BaseModel):
    """Universal scraped item returned by all site adapters."""

    id: str
    site_id: str
    url: str
    text: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    timestamp: datetime
    extra: dict[str, Any] = {}


class CrawlResult(BaseModel):
    """Summary of a crawl operation."""

    site_id: str
    items_found: int
    items_new: int
    duration_seconds: float


class SiteAdapterConfig(BaseModel):
    """Metadata about a site adapter, used by list-sites command."""

    site_id: str
    display_name: str
    login_url: str
    requires_login: bool
    description: str = ""
