import sys
from collections.abc import Callable
from typing import Any, ClassVar

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

# JavaScript executed after page load to expand "See more" buttons.
_FB_PAGE_SCRIPT = """
() => {
    for (const el of document.querySelectorAll('[role="button"]')) {
        const t = el.innerText || '';
        if (t.includes('查看更多') || t.toLowerCase().includes('see more')) {
            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }
    }
}
"""

# JavaScript that returns a clean HTML string containing only the post body.
# Picks the div[dir="auto"] with the most text content (= main post body).
_FB_CONTENT_SCRIPT = """
() => {
    const divs = [...document.querySelectorAll('div[dir="auto"]')];
    if (!divs.length) return null;
    const longest = divs.reduce((a, b) =>
        (a.innerText || '').length >= (b.innerText || '').length ? a : b
    );
    return '<div>' + longest.innerHTML + '</div>';
}
"""


class FacebookAdapter(SiteAdapter):
    """Adapter for crawling Facebook posts via headless browser.

    Uses page-mode (HTML → Markdown) with saved login state.
    Login state is stored at ~/.horus/states/facebook.json via `horus login facebook`.
    """

    site_id: ClassVar[str] = "facebook"
    display_name: ClassVar[str] = "Facebook"
    login_url: ClassVar[str] = "https://www.facebook.com/login"
    requires_login: ClassVar[bool] = True
    has_page_mode: ClassVar[bool] = True
    description: ClassVar[str] = "Crawl Facebook posts (requires login)"

    # Selector to wait for before extracting content.
    wait_for_selector: ClassVar[str] = "[role='main']"

    # JS executed after page load to expand truncated content.
    page_script: ClassVar[str] = _FB_PAGE_SCRIPT

    # JS that returns only the post body HTML (bypasses FB layout noise).
    content_script: ClassVar[str] = _FB_CONTENT_SCRIPT

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        # Page-mode: no response interception
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        # Page-mode: content extracted via page.content(), not response bodies
        return []

    def get_urls(self, **kwargs: Any) -> list[str]:
        url: str | None = kwargs.get("url")
        if url:
            return [url]
        if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
            try:
                lines = [
                    line.strip()
                    for line in sys.stdin
                    if line.strip() and not line.startswith("#")
                ]
                if lines:
                    return lines
            except OSError:
                pass
        raise ValueError("facebook adapter requires --url")

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "--url",
                "help": "Facebook post URL to crawl",
                "required": False,
                "default": None,
            }
        ]
