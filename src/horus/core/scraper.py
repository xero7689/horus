import asyncio
import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from bs4 import BeautifulSoup
from markdownify import markdownify
from playwright.async_api import Page, Response

from horus.core.browser import BaseBrowser
from horus.models import ScrapedItem, ScrapedPage

_STRIP_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript"]


class CommentParser(Protocol):
    def __call__(self, html: str, *, post_pk: str) -> list[ScrapedItem]: ...


def _html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    # Extract main content if available, otherwise fall back to body
    main = soup.find("main") or soup.find("article") or soup.find("body") or soup
    return markdownify(str(main), heading_style="ATX")


def count_new_items(items: list[ScrapedItem], seen: set[str], *, since: datetime | None) -> int:
    """Count items scrape() will return: unseen ids newer than `since`.

    Mutates `seen`. Mirrors the post-loop dedup + since filter so the in-loop
    count matches what scrape() finally returns.
    """
    counted = 0
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        if since is None or item.timestamp > since:
            counted += 1
    return counted


def limit_reached(counted: int, limit: int) -> bool:
    """True once `counted` meets `limit`; limit 0 means unlimited."""
    return limit > 0 and counted >= limit


class BaseScraper(BaseBrowser):
    """Generic scroll-and-intercept scraper. Subclasses inject strategy via adapters."""

    def __init__(
        self,
        *,
        headless: bool = True,
        scroll_delay_min: float = 3.0,
        scroll_delay_max: float = 8.0,
        request_jitter: float = 2.0,
        max_pages: int = 50,
    ) -> None:
        super().__init__(headless=headless)
        self._scroll_delay_min = scroll_delay_min
        self._scroll_delay_max = scroll_delay_max
        self._request_jitter = request_jitter
        self._max_pages = max_pages

    async def scrape(
        self,
        url: str,
        response_filter: Callable[[str, dict[str, Any]], bool],
        parser: Callable[[dict[str, Any]], list[ScrapedItem]],
        state_path: Path | None = None,
        *,
        since: datetime | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_batch: Callable[[list[ScrapedItem]], None] | None = None,
        limit: int = 0,
    ) -> list[ScrapedItem]:
        """Navigate to URL, intercept responses, scroll, parse, return items.

        Logic ported from playright-playground _scrape_page:
        - early bailout when all initial items are older than `since`
        - consecutive-empty 3 times → stop scrolling
        - `since` cutoff stops scrolling when oldest item <= since
        - `limit` > 0: stop scrolling once that many unseen items newer than
          `since` have been collected (0 = unlimited; a stop threshold — the
          last batch is kept whole, so the result may slightly exceed it)
        - dedup by id
        - descending sort by timestamp
        """
        assert self._browser is not None, "Use as async context manager"

        all_items: list[ScrapedItem] = []
        seen_ids: set[str] = set()
        counted = 0
        collected_responses: list[dict[str, Any]] = []

        async def on_response(response: Response) -> None:
            try:
                body = await response.json()
                if response_filter(response.url, body):
                    collected_responses.append(body)
            except Exception:
                pass

        context = await self.new_context(state_path=state_path)
        page = await context.new_page()
        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="load", timeout=60000)

            # Wait for intercepted responses to arrive
            for _ in range(30):
                if collected_responses:
                    break
                await asyncio.sleep(1)
            await asyncio.sleep(random.uniform(2, 4))

            # Parse initial load
            for resp in collected_responses:
                all_items.extend(parser(resp))
            if on_batch and all_items:
                on_batch(list(all_items))
            counted += count_new_items(all_items, seen_ids, since=since)

            # Early bailout: if all initial items are older than since, or the
            # initial load already meets the limit, skip scrolling
            if (since and all_items and all(item.timestamp <= since for item in all_items)) or (
                limit_reached(counted, limit)
            ):
                pass  # skip scroll loop entirely
            else:
                # Scroll to load more
                scroll_count = 0
                no_new_count = 0  # consecutive scrolls with no new data

                while scroll_count < self._max_pages:
                    prev_resp_count = len(collected_responses)
                    prev_item_count = len(all_items)

                    await self._scroll_page(page)
                    scroll_count += 1

                    # Wait for new response
                    await asyncio.sleep(0.5)
                    for _ in range(10):
                        if len(collected_responses) > prev_resp_count:
                            break
                        await asyncio.sleep(0.5)

                    # Parse new responses
                    new_items: list[ScrapedItem] = []
                    for resp in collected_responses[prev_resp_count:]:
                        new_items.extend(parser(resp))
                    all_items.extend(new_items)
                    if on_batch and new_items:
                        on_batch(new_items)
                    counted += count_new_items(new_items, seen_ids, since=since)

                    if on_progress:
                        on_progress(scroll_count, len(all_items))

                    # Stop if no new items after scrolling (3 consecutive empties)
                    if len(all_items) == prev_item_count:
                        no_new_count += 1
                        if no_new_count >= 3:
                            break
                    else:
                        no_new_count = 0

                    # Stop if we've scrolled past the since cutoff
                    if since and all_items:
                        oldest = min(item.timestamp for item in all_items)
                        if oldest <= since:
                            break

                    # Stop once the limit is met
                    if limit_reached(counted, limit):
                        break
        finally:
            await context.close()

        # Deduplicate by item id
        seen: set[str] = set()
        unique_items: list[ScrapedItem] = []
        for item in all_items:
            if item.id not in seen:
                seen.add(item.id)
                unique_items.append(item)

        # Filter by since
        if since:
            unique_items = [item for item in unique_items if item.timestamp > since]

        # Sort descending by timestamp
        unique_items.sort(key=lambda item: item.timestamp, reverse=True)
        return unique_items

    async def scrape_page(
        self,
        url: str,
        state_path: Path | None = None,
        *,
        site_id: str = "web",
        wait_for: str | None = None,
        page_script: str | None = None,
        content_script: str | None = None,
    ) -> ScrapedPage:
        """Navigate to URL, extract full rendered HTML, convert to Markdown.

        Args:
            page_script: Optional JavaScript evaluated after load, before extraction.
                Useful for clicking "See more" buttons or other DOM mutations.
            content_script: Optional JavaScript that returns a string — used instead of
                full page.content() for targeted content extraction (e.g. Facebook
                post body only). Must return a string or null.
        """
        assert self._browser is not None, "Use as async context manager"

        context = await self.new_context(state_path=state_path)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # Wait for network to settle (handles JS-heavy sites like Substack)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # timeout is okay — grab whatever rendered
            if wait_for:
                await page.wait_for_selector(wait_for, timeout=10000)
            if page_script:
                try:
                    await page.evaluate(page_script)
                    await asyncio.sleep(1)  # let DOM settle after script
                except Exception:
                    pass  # script failure is non-fatal
            title = await page.title()
            if content_script:
                try:
                    extracted = await page.evaluate(content_script)
                    html = str(extracted) if extracted else await page.content()
                except Exception:
                    html = await page.content()
            else:
                html = await page.content()
        finally:
            await context.close()

        markdown = _html_to_markdown(html)
        return ScrapedPage(url=url, site_id=site_id, title=title, markdown=markdown)

    async def scrape_comments(
        self,
        url: str,
        post_pk: str,
        parser: CommentParser,
        state_path: Path | None = None,
    ) -> list[ScrapedItem]:
        """Navigate to a post URL and extract comments from SSR HTML.

        Args:
            url: Full URL of the post page.
            post_pk: PK of the post (passed to parser as keyword arg).
            parser: Callable(html, *, post_pk) -> list[ScrapedItem].
            state_path: Optional saved browser auth state.

        Returns:
            List of ScrapedItems parsed from the page HTML.
        """
        assert self._browser is not None, "Use as async context manager"

        context = await self.new_context(state_path=state_path)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            html = await page.content()
        finally:
            await context.close()

        return parser(html, post_pk=post_pk)

    async def fetch_inline_json(
        self,
        url: str,
        extractor: Callable[[str], dict[str, Any]],
        state_path: Path | None = None,
    ) -> dict[str, Any]:
        """Navigate to `url`, hand the rendered HTML to `extractor`, return its dict result.

        For pages that inline their data as JSON in <script> tags rather than firing an
        interceptable XHR (e.g. an Instagram post opened by direct URL). The extractor is
        adapter-supplied so page-shape knowledge stays in the adapter.
        """
        assert self._browser is not None, "Use as async context manager"
        context = await self.new_context(state_path=state_path)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # timeout is fine — inlined JSON is present at initial render
            html = await page.content()
        finally:
            await context.close()
        return extractor(html)

    async def scrape_comments_via_grid(
        self,
        profile_url: str,
        response_filter: Callable[[str, dict[str, Any]], bool],
        parser: Callable[[dict[str, Any]], list[ScrapedItem]],
        state_path: Path | None = None,
        *,
        max_posts: int = 12,
        target_codes: list[str] | None = None,
        thumbnail_selector: str = 'a[href*="/p/"]',
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[ScrapedItem]:
        """Capture comments by walking a profile grid and opening each post as an in-app modal.

        Instagram only fires the ``/api/v1/media/<id>/comments/`` (and ``/info/``) XHRs when a
        post is opened as a client-side MODAL from the profile grid — a direct navigation to
        ``/p/<code>/`` inlines the post and fires nothing interceptable. So we load the profile
        and open posts as modals (closing each with Escape), parsing whatever the
        ``response_filter`` accepts. Two modes:
        - ``target_codes`` given → find & open each specific post's thumbnail (scrolling the
          grid to load it), for exact posts (e.g. a single ``--url`` crawl).
        - otherwise → open the first ``max_posts`` thumbnails in grid order (recent posts).

        Returns all parsed items (posts from ``/info/`` and comments from ``/comments/``),
        deduped by id. Callers attribute comments to posts via ``parent_post_id`` (== media_id).
        """
        assert self._browser is not None, "Use as async context manager"

        collected: list[dict[str, Any]] = []

        async def on_response(response: Response) -> None:
            try:
                body = await response.json()
                if response_filter(response.url, body):
                    collected.append(body)
            except Exception:
                pass

        context = await self.new_context(state_path=state_path)
        page = await context.new_page()
        page.on("response", on_response)

        async def _open_and_capture(locator: Any) -> None:
            """Click a thumbnail, wait for its modal XHRs, then close the modal."""
            prev = len(collected)
            try:
                await locator.scroll_into_view_if_needed(timeout=5000)
                await locator.click(timeout=8000)
            except Exception:
                return  # thumbnail vanished / not clickable — skip
            for _ in range(12):  # wait for the modal's comments/info XHRs to arrive
                if len(collected) > prev:
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(random.uniform(1, 2))
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.5, 1.5))

        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))

            if target_codes:
                for idx, code in enumerate(target_codes):
                    # A post's grid anchor may be /p/<code>/ or /reel/<code>/ — match both,
                    # else a reel's comments would be silently skipped after 20 dry scrolls.
                    thumb = page.locator(f'a[href*="/p/{code}/"], a[href*="/reel/{code}/"]').first
                    # scroll the grid until this specific post's thumbnail loads
                    for _ in range(20):
                        if await thumb.count() > 0:
                            break
                        await page.mouse.wheel(0, 3000)
                        await asyncio.sleep(1.2)
                    if await thumb.count() == 0:
                        continue  # too old to reach via grid — skip
                    await _open_and_capture(thumb)
                    if on_progress:
                        on_progress(idx + 1, len(collected))
            else:
                thumbnails = page.locator(thumbnail_selector)
                walk = min(max_posts, await thumbnails.count())
                for i in range(walk):
                    await _open_and_capture(thumbnails.nth(i))
                    if on_progress:
                        on_progress(i + 1, len(collected))
        finally:
            await context.close()

        items: list[ScrapedItem] = []
        for body in collected:
            items.extend(parser(body))

        seen: set[str] = set()
        unique: list[ScrapedItem] = []
        for item in items:
            if item.id not in seen:
                seen.add(item.id)
                unique.append(item)
        return unique

    async def _scroll_page(self, page: Page) -> None:
        """Scroll to bottom with random delay."""
        delay = random.uniform(
            self._scroll_delay_min,
            self._scroll_delay_max,
        ) + random.uniform(0, self._request_jitter)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(delay)
