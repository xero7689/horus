from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, ClassVar

from horus.models import ScrapedItem, SiteAdapterConfig

if TYPE_CHECKING:
    from pathlib import Path

    from horus.core.scraper import BaseScraper, CommentParser


class SiteAdapter(ABC):
    """Base class for all site adapters.

    To add a new site:
    1. Create src/horus/adapters/mysite.py, subclass SiteAdapter
    2. Implement get_urls() (the only abstract method); interception adapters also
       override get_response_filter() and parse_response()
    3. In adapters/__init__.py, import and call register(MySiteAdapter)

    Mode flags (mutually exclusive):
    - has_page_mode=True : use scrape_page() → stores to pages table
    - has_http_mode=True  : use fetch_items() → direct HTTP, no Playwright
    - both False          : use scrape() response intercept → stores to items table
    """

    site_id: ClassVar[str]
    display_name: ClassVar[str]
    login_url: ClassVar[str]
    requires_login: ClassVar[bool]
    description: ClassVar[str] = ""
    has_page_mode: ClassVar[bool] = False  # True = use scrape_page() instead of scrape()
    has_http_mode: ClassVar[bool] = False  # True = use fetch_items(), no Playwright needed

    # Page-mode hooks (used only when has_page_mode=True); declared here so callers
    # never need getattr — undeclared hooks are how serve drifted from cli.
    wait_for_selector: ClassVar[str | None] = None
    page_script: ClassVar[str | None] = None
    content_script: ClassVar[str | None] = None

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        """(response_url, response_body) -> bool. Override in interception adapters."""
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        """Parse an intercepted response body. Override in interception adapters."""
        return []

    @abstractmethod
    def get_urls(self, **kwargs: Any) -> list[str]:
        """Get URLs to crawl given CLI parameters."""
        ...

    def get_crawl_options(self) -> list[dict[str, Any]]:
        """Site-specific CLI options for `horus crawl <site>`.

        Each dict: {"name": "--user", "help": "...", "required": False, "default": None}
        """
        return []

    def is_post_url(self, url: str) -> bool:
        """True if `url` points to a single post/permalink rather than a profile/listing.

        Interception adapters can use this to route a single-post URL through a
        different fetch path (e.g. SSR-inlined JSON) than a scrollable profile.
        """
        return False

    def extract_inline_items(self, html: str) -> dict[str, Any]:
        """Extract a parse_response-compatible body from a page's inlined JSON.

        Used for single-post URLs whose data is server-inlined instead of fired as an
        interceptable XHR. Return {} if nothing usable is found. Default: nothing.
        """
        return {}

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        """Fetch items via direct HTTP (no Playwright). Override when has_http_mode=True."""
        raise NotImplementedError(f"{self.__class__.__name__} does not implement fetch_items()")

    def post_process(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        """Optional post-processing hook after crawl."""
        return items

    async def fetch_comments(
        self,
        scraper: "BaseScraper",
        posts: list[ScrapedItem],
        state_path: "Path | None",
        *,
        via_post_url: bool = False,
        on_event: Callable[[str], None] | None = None,
    ) -> "AsyncIterator[tuple[ScrapedItem, list[ScrapedItem]]]":
        """Yield (post, comments) pairs for --with-comments.

        Default strategy: per-post HTML navigation via get_comment_parser()
        (threads). Adapters with bespoke choreography (e.g. Instagram's
        profile-grid modal walk) override this. Errors for one post must not
        abort the rest — report via on_event and continue.

        Contract: implementations only FETCH and ATTRIBUTE comments (including
        url backfill). They must not persist, emit, or post_process — the
        orchestrator owns all storage/reporting side effects.
        """
        parser = self.get_comment_parser()
        for post in posts:
            try:
                comments = (
                    await scraper.scrape_comments(
                        url=post.url, post_pk=post.id, parser=parser, state_path=state_path
                    )
                    if parser
                    else []
                )
            except Exception as e:  # noqa: BLE001 — one bad post must not kill the walk
                if on_event:
                    on_event(
                        f"  [yellow]Warning: failed to fetch comments for {post.url}: {e}[/yellow]"
                    )
                continue
            yield post, comments

    def get_comment_parser(self) -> "CommentParser | None":
        """Return a comment parser callable, or None if comments are not supported.

        The callable signature: (html: str, *, post_pk: str) -> list[ScrapedItem]
        Override in adapters that support --with-comments.
        """
        return None

    @classmethod
    def get_config(cls) -> SiteAdapterConfig:
        return SiteAdapterConfig(
            site_id=cls.site_id,
            display_name=cls.display_name,
            login_url=cls.login_url,
            requires_login=cls.requires_login,
            description=cls.description,
        )
