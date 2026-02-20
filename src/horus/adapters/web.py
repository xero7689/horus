from collections.abc import Callable
from pathlib import Path
from typing import Any

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem


class GenericWebAdapter(SiteAdapter):
    """Adapter for crawling any public webpage as Markdown.

    Unlike API-interception adapters (e.g. Threads), this adapter:
    - Does not intercept HTTP responses
    - Uses scrape_page() to get full rendered HTML → Markdown
    - Stores results in the `pages` table, not `items`
    """

    site_id = "web"
    display_name = "Generic Web Page"
    login_url = ""
    requires_login = False
    has_page_mode = True
    description = "Crawl any public webpage and convert HTML to Markdown"

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        # Page-mode adapter: never intercept responses
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        # Page-mode adapter: parsing is done via page.content(), not response interception
        return []

    def get_urls(self, **kwargs: Any) -> list[str]:
        url: str | None = kwargs.get("url")
        url_list: str | None = kwargs.get("url_list")

        if url:
            return [url]
        if url_list:
            path = Path(url_list)
            if not path.exists():
                raise ValueError(f"URL list file not found: {url_list}")
            lines = path.read_text().splitlines()
            urls = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
            if not urls:
                raise ValueError(f"No URLs found in {url_list}")
            return urls
        raise ValueError("web adapter requires --url or --url-list")

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "--url",
                "help": "URL to crawl",
                "required": False,
                "default": None,
            },
            {
                "name": "--url-list",
                "help": "Text file with URLs, one per line (# for comments)",
                "required": False,
                "default": None,
            },
        ]
