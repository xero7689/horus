"""Single source of truth for crawl orchestration, shared by cli and serve.

The three-way mode dispatch (http / page / intercept), storage side-effects and
comment fetching live here. Frontends differ only in their CrawlReporter.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem, ScrapedPage


class CrawlReporter(Protocol):
    """Frontend-specific output. All methods must be non-blocking and cheap."""

    def log(self, message: str) -> None:
        """Human-readable progress line (Rich console / SSE log event)."""
        ...

    def progress(self, scroll: int, total: int) -> None:
        """Scroll progress during an intercept crawl."""
        ...

    def emit(self, item: ScrapedItem | ScrapedPage) -> None:
        """Per-item emission (CLI --stdout JSONL). Serve: no-op."""
        ...


class NullReporter:
    def log(self, message: str) -> None: ...
    def progress(self, scroll: int, total: int) -> None: ...
    def emit(self, item: ScrapedItem | ScrapedPage) -> None: ...


@dataclass
class CrawlOutcome:
    total_found: int = 0
    total_new: int = 0
    interrupted: bool = False


class CrawlOrchestrator:
    def __init__(
        self,
        storage: HorusStorage,
        settings: Settings,
        *,
        scraper_cls: type[BaseScraper] = BaseScraper,
        reporter: CrawlReporter | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._scraper_cls = scraper_cls
        self._reporter = reporter or NullReporter()

    async def run(
        self,
        adapter: SiteAdapter,
        urls: list[str],
        *,
        adapter_cls: type[SiteAdapter] | None = None,
        site: str,
        kwargs: dict[str, Any],
        limit: int,
        since: datetime | None,
        state_path: Path | None,
        output_dir: Path | None = None,
        write_page_md: Any | None = None,  # Callable[[ScrapedPage, Path], None]
        outcome: CrawlOutcome | None = None,
    ) -> CrawlOutcome:
        """Run one crawl. Mutates (and returns) the caller-owned outcome.

        Catches nothing — KeyboardInterrupt / CancelledError / errors propagate
        to the caller, which owns process-exit and interrupted-summary UX.
        """
        cls = adapter_cls if adapter_cls is not None else type(adapter)
        outcome = outcome if outcome is not None else CrawlOutcome()

        started_at = datetime.now(UTC)

        scraper_kwargs: dict[str, Any] = {
            "headless": self._settings.headless,
            "scroll_delay_min": self._settings.scroll_delay_min,
            "scroll_delay_max": self._settings.scroll_delay_max,
            "request_jitter": self._settings.request_jitter,
            "max_pages": self._settings.max_pages,
        }

        if cls.has_http_mode:
            await self._run_http(
                adapter,
                site=site,
                kwargs=kwargs,
                limit=limit,
                outcome=outcome,
                started_at=started_at,
            )
        else:
            async with self._scraper_cls(**scraper_kwargs) as scraper:
                if cls.has_page_mode:
                    await self._run_pages(
                        cls,
                        scraper,
                        urls,
                        site=site,
                        state_path=state_path,
                        output_dir=output_dir,
                        write_page_md=write_page_md,
                        outcome=outcome,
                        started_at=started_at,
                    )
                else:
                    await self._run_intercept(
                        adapter,
                        scraper,
                        urls,
                        site=site,
                        kwargs=kwargs,
                        since=since,
                        state_path=state_path,
                        outcome=outcome,
                        started_at=started_at,
                    )

        return outcome

    async def _run_http(
        self,
        adapter: SiteAdapter,
        *,
        site: str,
        kwargs: dict[str, Any],
        limit: int,
        outcome: CrawlOutcome,
        started_at: datetime,
    ) -> None:
        # Direct HTTP path (no Playwright): DDG, serper, etc.
        self._reporter.log(f"Searching [cyan]{site}[/cyan]...")
        # Forward the top-level --limit into http-mode adapters. It lands in the
        # `limit` local (a Click option), not in kwargs, so http adapters never
        # saw it. kwargs wins on collision to keep the serve path correct.
        http_kwargs = {"limit": limit, **kwargs}
        items = await adapter.fetch_items(**http_kwargs)
        items = adapter.post_process(items)
        new_count = self._storage.upsert_items(items)
        outcome.total_found = len(items)
        outcome.total_new = new_count
        self._storage.log_crawl(
            site, str(kwargs), outcome.total_found, outcome.total_new, started_at
        )
        for item in items:
            self._reporter.emit(item)

    async def _run_pages(
        self,
        cls: type[SiteAdapter],
        scraper: BaseScraper,
        urls: list[str],
        *,
        site: str,
        state_path: Path | None,
        output_dir: Path | None,
        write_page_md: Any | None,
        outcome: CrawlOutcome,
        started_at: datetime,
    ) -> None:
        # Page-based path: HTML → Markdown
        wait_for = cls.wait_for_selector
        page_script = cls.page_script
        content_script = cls.content_script
        for url in urls:
            self._reporter.log(f"Fetching [cyan]{url}[/cyan]...")
            page = await scraper.scrape_page(
                url,
                state_path,
                site_id=site,
                wait_for=wait_for,
                page_script=page_script,
                content_script=content_script,
            )
            is_new = self._storage.upsert_page(page)
            outcome.total_found += 1
            outcome.total_new += int(is_new)
            if output_dir and write_page_md:
                write_page_md(page, output_dir)
                self._reporter.log(f"  [dim]→ saved markdown to {output_dir}[/dim]")
            self._reporter.emit(page)
        self._storage.log_crawl(
            site,
            urls[0] if len(urls) == 1 else f"{len(urls)} URLs",
            outcome.total_found,
            outcome.total_new,
            started_at,
        )

    async def _run_intercept(
        self,
        adapter: SiteAdapter,
        scraper: BaseScraper,
        urls: list[str],
        *,
        site: str,
        kwargs: dict[str, Any],
        since: datetime | None,
        state_path: Path | None,
        outcome: CrawlOutcome,
        started_at: datetime,
    ) -> None:
        # Response-intercept path (Threads etc.)
        if since is None:
            since = self._storage.get_latest_timestamp(site)

        with_comments = kwargs.get("with_comments") == "true"
        # Populated in on_batch when with_comments is True
        crawled_top_level: list[ScrapedItem] = []

        def on_progress(scroll: int, total: int) -> None:
            self._reporter.progress(scroll, total)

        def on_batch(batch: list[ScrapedItem]) -> None:
            # URL-less replies are unattributed comments: an intercepted comment has
            # url="" until --with-comments attribution backfills it, and must never
            # persist from a primary batch. (Threads/twitter replies carry URLs and
            # are unaffected.) This guard runs unconditionally — with or without
            # --with-comments — matching the old IG flag guard's semantics.
            batch = [p for p in batch if not (p.extra.get("is_reply") and not p.url)]
            if not batch:
                return
            batch = adapter.post_process(batch)
            new_count = self._storage.upsert_items(batch)
            outcome.total_found += len(batch)
            outcome.total_new += new_count
            for item in batch:
                self._reporter.emit(item)
            # Collect top-level posts for comment fetch. The is_reply filter is
            # still needed for the non-interception (HTML parser) path, where the
            # guard above is skipped and replies may reach this batch.
            if with_comments:
                crawled_top_level.extend(p for p in batch if not p.extra.get("is_reply", False))

        crawled_via_post_url = False
        for url in urls:
            self._reporter.log(f"Crawling [cyan]{url}[/cyan]...")
            if adapter.is_post_url(url):
                # Single post: IG inlines the post as JSON in the page (direct
                # navigation fires no interceptable XHR), so pull it from SSR.
                crawled_via_post_url = True
                blob = await scraper.fetch_inline_json(
                    url, adapter.extract_inline_items, state_path
                )
                items = adapter.parse_response(blob)
                if items:
                    on_batch(items)
                else:
                    self._reporter.log(f"  [yellow]No post data found at {url}[/yellow]")
            else:
                await scraper.scrape(
                    url,
                    adapter.get_response_filter(),
                    adapter.parse_response,
                    state_path,
                    since=since,
                    on_progress=on_progress,
                    on_batch=on_batch,
                )
            self._storage.log_crawl(site, url, outcome.total_found, outcome.total_new, started_at)

        if with_comments and crawled_top_level:
            self._reporter.log(f"Fetching comments for {len(crawled_top_level)} posts...")
            async for post, comment_items in adapter.fetch_comments(
                scraper,
                crawled_top_level,
                state_path,
                via_post_url=crawled_via_post_url,
                on_event=self._reporter.log,
            ):
                if not comment_items:
                    continue
                new_count = self._storage.upsert_items(comment_items)
                outcome.total_found += len(comment_items)
                outcome.total_new += new_count
                self._reporter.log(
                    f"  [dim]{post.url}: {len(comment_items)} comments ({new_count} new)[/dim]"
                )
