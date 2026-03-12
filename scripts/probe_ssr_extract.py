#!/usr/bin/env python3
"""Extract and analyze SSR thread_items data from Threads post page HTML."""

import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

STATE_PATH = Path.home() / ".horus" / "states" / "threads.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TARGET_URL = "https://www.threads.com/@meow.coder/post/DVmeKU6k-46"


def _extract_require_blocks(html: str) -> list[dict]:
    """Extract JSON from __bbox require blocks in Threads SSR HTML."""
    # Threads embeds data in: requireLazy(["ScheduledServerJS",...],function(_) { ... __bbox ... })
    # or in: <script>require("ScheduledServerJS").handle(...)</script>
    results = []

    # Look for JSON blobs containing thread_items
    # The data is typically in a script tag like:
    # {"__bbox":{"require":[["ScheduledServerJS","handle",null,[{"__bbox":...}]]]}}
    pattern = r'("thread_items"\s*:\s*\[)'
    matches = list(re.finditer(pattern, html))
    print(f"Found {len(matches)} 'thread_items' occurrences")

    for m in matches:
        start = m.start()
        # Go back to find start of surrounding JSON object
        # Find the nearest '{' before thread_items
        chunk_start = max(0, start - 2000)
        chunk = html[chunk_start:start + 50000]

        # Try to find and parse the thread_items array
        ti_start = chunk.find('"thread_items"')
        if ti_start < 0:
            continue

        # Find the array start
        arr_start = chunk.find('[', ti_start)
        if arr_start < 0:
            continue

        # Balance brackets to find end
        depth = 0
        end = arr_start
        for i, ch in enumerate(chunk[arr_start:], arr_start):
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        try:
            arr_json = chunk[arr_start:end]
            data = json.loads(arr_json)
            results.append(data)
        except json.JSONDecodeError:
            pass

    return results


async def probe() -> None:
    storage_state = str(STATE_PATH) if STATE_PATH.exists() else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        page = await context.new_page()
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        html = await page.content()
        await browser.close()

    print(f"HTML length: {len(html)} chars")

    # Extract thread_items arrays
    thread_arrays = _extract_require_blocks(html)
    print(f"Extracted {len(thread_arrays)} thread_items arrays\n")

    for i, arr in enumerate(thread_arrays):
        print(f"\n{'='*60}")
        print(f"thread_items array #{i+1}: {len(arr)} items")
        for j, item in enumerate(arr):
            print(f"\n  Item #{j+1} keys: {list(item.keys()) if isinstance(item, dict) else type(item)}")
            if isinstance(item, dict):
                post = item.get("post", {})
                if post:
                    user = post.get("user", {})
                    caption = post.get("caption") or {}
                    text_post_info = post.get("text_post_app_info", {}) or {}
                    print(f"    post.pk: {post.get('pk')}")
                    print(f"    post.taken_at: {post.get('taken_at')}")
                    print(f"    post.user.username: {user.get('username') if user else None}")
                    print(f"    post.caption.text: {caption.get('text', '')[:100] if caption else None}")
                    print(f"    post.like_count: {post.get('like_count')}")
                    reply_to = text_post_info.get("reply_to_author") or {}
                    print(f"    post.text_post_app_info.reply_to_author: {reply_to.get('username') if reply_to else None}")
                    print(f"    post.text_post_app_info.direct_reply_count: {text_post_info.get('direct_reply_count')}")
                    print(f"    post keys: {list(post.keys())}")

    # Save the full arrays
    out = Path("/tmp/threads_ssr_thread_items.json")
    out.write_text(json.dumps(thread_arrays, indent=2, ensure_ascii=False, default=str))
    print(f"\nFull data saved to {out}")


if __name__ == "__main__":
    asyncio.run(probe())
