#!/usr/bin/env python3
"""Direct smoke test: scrape_comments() on a known post."""
import asyncio
from pathlib import Path

from horus.adapters.threads import parse_comments_from_html
from horus.core.scraper import BaseScraper

STATE_PATH = Path.home() / ".horus" / "states" / "threads.json"
# Known post with comments (from our earlier probe)
POST_URL = "https://www.threads.com/@meow.coder/post/DVmeKU6k-46"
POST_PK = "3847895582682377786"


async def main() -> None:
    state = STATE_PATH if STATE_PATH.exists() else None
    async with BaseScraper(headless=True) as scraper:
        print(f"Scraping comments from: {POST_URL}")
        items = await scraper.scrape_comments(
            url=POST_URL,
            post_pk=POST_PK,
            parser=parse_comments_from_html,
            state_path=state,
        )

    print(f"\nFound {len(items)} comments:")
    for item in items[:10]:
        print(f"  @{item.author_name}: {(item.text or '')[:80]}")
        print(f"    is_reply={item.extra.get('is_reply')} parent={item.extra.get('parent_post_id')} conv={item.extra.get('conversation_id')}")


if __name__ == "__main__":
    asyncio.run(main())
