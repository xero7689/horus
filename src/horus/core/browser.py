import asyncio
from pathlib import Path
from types import TracebackType
from typing import Self

from playwright.async_api import Browser, BrowserContext, Playwright, ViewportSize, async_playwright

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_DEFAULT_VIEWPORT: ViewportSize = {"width": 1280, "height": 720}


class BaseBrowser:
    """Manages Playwright lifecycle and browser state persistence."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_context(
        self,
        *,
        state_path: Path | None = None,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> BrowserContext:
        """Create a browser context, optionally loading saved auth state."""
        assert self._browser is not None
        storage_state = str(state_path) if state_path and state_path.exists() else None
        return await self._browser.new_context(
            user_agent=user_agent,
            viewport=_DEFAULT_VIEWPORT,
            storage_state=storage_state,
        )

    async def save_login_state(
        self,
        login_url: str,
        output_path: Path,
        *,
        prompt: str = "Press Enter when you are done logging in...",
    ) -> None:
        """Open a non-headless browser for manual login, then save state."""
        assert self._playwright is not None
        browser = await self._playwright.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=_DEFAULT_USER_AGENT,
            viewport=_DEFAULT_VIEWPORT,
        )
        page = await context.new_page()
        await page.goto(login_url)

        print("Please log in manually in the browser window.")
        print(prompt)
        await asyncio.get_event_loop().run_in_executor(None, input)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(output_path))
        await browser.close()
        print(f"Browser state saved to {output_path}")
