#!/usr/bin/env python3
"""Probe script: navigate to a Threads post and dump all intercepted GraphQL responses."""

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


def _summarize(obj: object, depth: int = 0) -> object:
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return {k: _summarize(v, depth + 1) for k, v in list(obj.items())[:10]}
    if isinstance(obj, list):
        if not obj:
            return []
        return [_summarize(obj[0], depth + 1), f"... ({len(obj)} items)"]
    return type(obj).__name__


async def probe() -> None:
    storage_state = str(STATE_PATH) if STATE_PATH.exists() else None
    if not storage_state:
        print("[WARNING] No login state found, proceeding without auth")

    responses: list[tuple[str, dict]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        page = await context.new_page()

        async def on_response(response: Response) -> None:
            if "graphql" not in response.url:
                return
            try:
                body = await response.json()
                responses.append((response.url, body))
            except Exception:
                pass

        page.on("response", on_response)

        print(f"Navigating to: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="load", timeout=60000)

        # Wait for initial responses
        for _ in range(15):
            if responses:
                break
            await asyncio.sleep(1)

        # Wait more to catch lazy-loaded comments
        await asyncio.sleep(5)

        await browser.close()

    print(f"\n{'='*60}")
    print(f"Intercepted {len(responses)} GraphQL responses")
    print(f"{'='*60}\n")

    for i, (resp_url, body) in enumerate(responses):
        print(f"--- Response #{i+1} ---")
        print(f"URL: {resp_url}")
        data = body.get("data", {})
        if isinstance(data, dict):
            print(f"data keys: {list(data.keys())}")
            for k, v in data.items():
                if isinstance(v, dict):
                    print(f"  data.{k} keys: {list(v.keys())}")
                elif isinstance(v, list):
                    print(f"  data.{k}: list[{len(v)}]")
        print("Structure summary:")
        print(json.dumps(_summarize(body), indent=2, ensure_ascii=False))
        print()

    out_path = Path("/tmp/threads_probe_responses.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(
            [{"url": u, "body": b} for u, b in responses],
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    print(f"Full responses saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(probe())
