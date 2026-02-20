from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from horus.models import ScrapedItem, SiteAdapterConfig


class SiteAdapter(ABC):
    """Base class for all site adapters.

    To add a new site:
    1. Create src/horus/adapters/mysite.py, subclass SiteAdapter
    2. Implement the 3 abstract methods
    3. In adapters/__init__.py, import and call register(MySiteAdapter)
    """

    site_id: ClassVar[str]
    display_name: ClassVar[str]
    login_url: ClassVar[str]
    requires_login: ClassVar[bool]
    description: ClassVar[str] = ""
    has_page_mode: ClassVar[bool] = False  # True = use scrape_page() instead of scrape()

    @abstractmethod
    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        """Return (response_url, response_body) -> bool filter function."""
        ...

    @abstractmethod
    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        """Parse an intercepted response body into ScrapedItems."""
        ...

    @abstractmethod
    def get_urls(self, **kwargs: Any) -> list[str]:
        """Get URLs to crawl given CLI parameters."""
        ...

    def get_crawl_options(self) -> list[dict[str, Any]]:
        """Site-specific CLI options for `horus crawl <site>`.

        Each dict: {"name": "--user", "help": "...", "required": False, "default": None}
        """
        return []

    def post_process(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        """Optional post-processing hook after crawl."""
        return items

    @classmethod
    def get_config(cls) -> SiteAdapterConfig:
        return SiteAdapterConfig(
            site_id=cls.site_id,
            display_name=cls.display_name,
            login_url=cls.login_url,
            requires_login=cls.requires_login,
            description=cls.description,
        )
