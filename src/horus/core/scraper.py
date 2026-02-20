import asyncio
import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Response

from horus.core.browser import BaseBrowser
from horus.models import ScrapedItem


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
    ) -> list[ScrapedItem]:
        """Navigate to URL, intercept responses, scroll, parse, return items.

        Logic ported from playright-playground _scrape_page:
        - early bailout when all initial items are older than `since`
        - consecutive-empty 3 times → stop scrolling
        - `since` cutoff stops scrolling when oldest item <= since
        - dedup by id
        - descending sort by timestamp
        """
        assert self._browser is not None, "Use as async context manager"

        all_items: list[ScrapedItem] = []
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

            # Early bailout: if all initial items are older than since, skip scrolling
            if since and all_items and all(item.timestamp <= since for item in all_items):
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

    async def _scroll_page(self, page: Page) -> None:
        """Scroll to bottom with random delay."""
        delay = random.uniform(
            self._scroll_delay_min,
            self._scroll_delay_max,
        ) + random.uniform(0, self._request_jitter)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(delay)
