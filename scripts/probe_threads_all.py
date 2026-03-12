#!/usr/bin/env python3
"""Probe all JSON responses (not just graphql) on a Threads post page."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import Response, async_playwright

STATE_PATH = Path.home() / ".horus" / "states" / "threads.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TARGET_URL = "https://www.threads.com/@meow.coder/post/DVmeKU6k-46"


async def probe() -> None:
    storage_state = str(STATE_PATH) if STATE_PATH.exists() else None

    responses: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        page = await context.new_page()

        async def on_response(response: Response) -> None:
            url = response.url
            content_type = response.headers.get("content-type", "")
            # Capture all JSON responses
            if "json" not in content_type and "graphql" not in url:
                return
            try:
                body = await response.json()
                responses.append({"url": url, "body": body})
            except Exception:
                pass

        page.on("response", on_response)

        print(f"Navigating to: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(5)

        # Check if there are comments in the HTML (SSR)
        html = await page.content()
        print(f"\nPage HTML length: {len(html)} chars")

        # Search for comment-related keywords in HTML
        for kw in ["thread_items", "mediaData", "taken_at", "caption", "replyData", "commentData"]:
            idx = html.find(kw)
            if idx >= 0:
                print(f"Found '{kw}' in HTML at pos {idx}: ...{html[max(0,idx-30):idx+100]}...")

        await browser.close()

    print(f"\nTotal JSON responses intercepted: {len(responses)}")

    # Show all unique data keys
    print("\nAll responses with 'text', 'caption', 'taken_at', or 'thread':")
    interesting_count = 0
    for i, entry in enumerate(responses):
        raw = json.dumps(entry["body"])
        if any(kw in raw for kw in ['"text"', '"caption"', '"taken_at"', '"thread_items"', '"edges"']):
            interesting_count += 1
            print(f"\n  Response #{i+1}: {entry['url']}")
            d = entry["body"].get("data", {})
            if isinstance(d, dict):
                print(f"  data keys: {list(d.keys())}")
            # Show first 500 chars
            print(f"  Body (first 800): {raw[:800]}")

    if interesting_count == 0:
        print("  None found.")

    # Save all
    out = Path("/tmp/threads_probe_all.json")
    out.write_text(json.dumps(responses, indent=2, ensure_ascii=False, default=str))
    print(f"\nAll responses saved to {out}")


if __name__ == "__main__":
    asyncio.run(probe())
