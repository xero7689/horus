# Threads Comments (US-001) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--with-comments` flag to `horus crawl threads` that fetches all comments for each post by parsing SSR HTML from the post's page.

**Architecture:** Threads embeds `thread_items` arrays directly in the SSR HTML of each post page. After crawling posts, if `--with-comments` is set, navigate to each post's URL, extract `thread_items` from the HTML via regex, parse them with the existing `_parse_item()` function, and store results in the `items` table. No new tables or schema changes needed.

**Tech Stack:** Python 3.13+, Playwright (headless Chromium), existing `_parse_item()` in `threads.py`, `re` stdlib for SSR extraction.

---

## Chunk 1: SSR comment extraction in threads.py

### Task 1: Add `parse_comments_from_html()` to threads.py

**Files:**
- Modify: `src/horus/adapters/threads.py`
- Test: `tests/adapters/test_threads_adapter.py`

The SSR HTML on a post page contains multiple `thread_items` JSON arrays embedded in `<script>` tags. Array #1 is the original post itself (skip it). Arrays #2+ are comment groups — each array's first item is a root comment, subsequent items are replies to it.

The parser must:
1. Use regex to find all `"thread_items":[...]` in the HTML
2. Skip the first array (original post)
3. For each remaining array: first item → `is_reply=True, parent_post_id=post_pk, conversation_id=post_pk`; subsequent items → `is_reply=True, parent_post_id=prev_item.id, conversation_id=post_pk`

- [ ] **Step 1: Write failing tests**

Add to `tests/adapters/test_threads_adapter.py`:

```python
import json
import re


# ---------------------------------------------------------------------------
# Helpers to build minimal SSR HTML fixture
# ---------------------------------------------------------------------------

def _make_ssr_html(thread_item_arrays: list[list[dict]]) -> str:
    """Wrap thread_items arrays in minimal SSR HTML that parse_comments_from_html can parse."""
    parts = []
    for arr in thread_item_arrays:
        parts.append(f'"thread_items":{json.dumps(arr)}')
    return "<script>" + ",".join(parts) + "</script>"


def _make_post_item(pk: str, username: str, text: str, taken_at: int = 1704067200, reply_to_username: str | None = None) -> dict:
    return {
        "post": {
            "pk": pk,
            "code": pk,
            "taken_at": taken_at,
            "user": {"pk": f"u_{pk}", "username": username},
            "caption": {"text": text},
            "media_type": 19,
            "like_count": 1,
            "text_post_app_info": {
                "direct_reply_count": 0,
                "repost_count": 0,
                "reply_to_author": {"username": reply_to_username} if reply_to_username else None,
            },
        }
    }


class TestParseCommentsFromHtml:
    def test_returns_empty_when_no_thread_items(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        result = parse_comments_from_html("<html></html>", post_pk="111")
        assert result == []

    def test_skips_first_array_which_is_original_post(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        comment1 = [_make_post_item("222", "bob", "First comment")]
        html = _make_ssr_html([original_post, comment1])
        items = parse_comments_from_html(html, post_pk="111")
        # Only the comment, not the original post
        assert len(items) == 1
        assert items[0].id == "222"

    def test_root_comment_has_correct_parent_and_conversation_id(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        comment = [_make_post_item("222", "bob", "First comment", reply_to_username="alice")]
        html = _make_ssr_html([original_post, comment])
        items = parse_comments_from_html(html, post_pk="111")
        item = items[0]
        assert item.extra["is_reply"] is True
        assert item.extra["parent_post_id"] == "111"
        assert item.extra["conversation_id"] == "111"

    def test_nested_reply_links_to_root_comment(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        thread = [
            _make_post_item("222", "bob", "Root comment", reply_to_username="alice"),
            _make_post_item("333", "carol", "Reply to bob", reply_to_username="bob"),
        ]
        html = _make_ssr_html([original_post, thread])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 2
        root = next(i for i in items if i.id == "222")
        nested = next(i for i in items if i.id == "333")
        assert root.extra["parent_post_id"] == "111"
        assert nested.extra["parent_post_id"] == "222"
        assert nested.extra["conversation_id"] == "111"

    def test_multiple_comment_groups(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        group1 = [_make_post_item("222", "bob", "Comment 1", reply_to_username="alice")]
        group2 = [_make_post_item("333", "carol", "Comment 2", reply_to_username="alice")]
        html = _make_ssr_html([original_post, group1, group2])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 2
        ids = {i.id for i in items}
        assert ids == {"222", "333"}

    def test_deduplicates_by_id(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        # Same comment appears twice in HTML (can happen with SSR quirks)
        dup = [_make_post_item("222", "bob", "Comment")]
        html = _make_ssr_html([original_post, dup, dup])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/adapters/test_threads_adapter.py::TestParseCommentsFromHtml -v
```

Expected: `ImportError` or `AttributeError` — `parse_comments_from_html` not yet defined.

- [ ] **Step 3: Implement `parse_comments_from_html()` in threads.py**

Add at the top of `src/horus/adapters/threads.py` after existing imports:

```python
import json
import re
```

Add this function (after `_parse_item`, before `ThreadsAdapter` class):

```python
def _extract_thread_items_arrays(html: str) -> list[list[dict[str, Any]]]:
    """Extract all thread_items arrays from SSR HTML using bracket-balanced parsing."""
    results: list[list[dict[str, Any]]] = []
    pattern = re.compile(r'"thread_items"\s*:\s*\[')
    for match in pattern.finditer(html):
        arr_start = match.end() - 1  # position of '['
        depth = 0
        end = arr_start
        for i in range(arr_start, min(arr_start + 200_000, len(html))):
            ch = html[i]
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            arr = json.loads(html[arr_start:end])
            if isinstance(arr, list):
                results.append(arr)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def parse_comments_from_html(html: str, *, post_pk: str) -> list[ScrapedItem]:
    """Parse SSR thread_items arrays from a Threads post page HTML.

    Skips the first array (original post). Each subsequent array is a comment
    group: first item is a root comment replying to the post, remaining items
    are nested replies.

    Args:
        html: Full rendered HTML of the post page.
        post_pk: The PK of the original post (used as conversation_id and
                 parent_post_id for root-level comments).

    Returns:
        List of ScrapedItems for all comments, deduplicated by id.
    """
    arrays = _extract_thread_items_arrays(html)
    if not arrays:
        return []

    # Skip first array — it's the original post
    comment_arrays = arrays[1:]

    seen: set[str] = set()
    items: list[ScrapedItem] = []

    for arr in comment_arrays:
        prev_id: str | None = post_pk
        for thread_item in arr:
            post_data = thread_item.get("post")
            if not post_data:
                continue
            item = _parse_item(
                post_data,
                parent_post_id=prev_id,
                conversation_id=post_pk,
                is_reply=True,
            )
            if item and item.id not in seen:
                seen.add(item.id)
                items.append(item)
                prev_id = item.id

    return items
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/adapters/test_threads_adapter.py::TestParseCommentsFromHtml -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
uv run pytest tests/ -v
```

Expected: All existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/horus/adapters/threads.py tests/adapters/test_threads_adapter.py
git commit -m "feat(threads): add parse_comments_from_html() for SSR comment extraction

Co-Authored-By: xero7689 <shzlee217@gmail.com>"
```

---

## Chunk 2: scraper.py — scrape_comments() method

### Task 2: Add `scrape_comments()` to BaseScraper

**Files:**
- Modify: `src/horus/core/scraper.py`
- Test: `tests/test_scraper_comments.py` (new file)

This method navigates to a post URL, waits for the page to render, grabs `page.content()`, and passes the HTML to a parser callback. It reuses the existing `scrape_page()` browser context logic (same auth state loading).

- [ ] **Step 1: Write failing test**

Create `tests/test_scraper_comments.py`:

```python
"""Unit tests for BaseScraper.scrape_comments() using a mock page."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from horus.core.scraper import BaseScraper
from horus.models import ScrapedItem
from datetime import datetime, UTC


def _make_item(pk: str) -> ScrapedItem:
    return ScrapedItem(
        id=pk,
        site_id="threads",
        url=f"https://www.threads.net/post/{pk}",
        text="test",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


class TestScrapeComments:
    @pytest.mark.asyncio
    async def test_calls_parser_with_html_and_returns_items(self) -> None:
        """scrape_comments() should pass page HTML to parser and return results."""
        expected_items = [_make_item("111"), _make_item("222")]
        mock_html = "<html>mock content</html>"

        def fake_parser(html: str, *, post_pk: str) -> list[ScrapedItem]:
            assert html == mock_html
            assert post_pk == "abc"
            return expected_items

        scraper = BaseScraper()
        scraper._browser = MagicMock()  # pretend we're inside context manager

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.content = AsyncMock(return_value=mock_html)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(scraper, "new_context", new_callable=AsyncMock, return_value=mock_context):
            items = await scraper.scrape_comments(
                url="https://www.threads.com/@alice/post/abc",
                post_pk="abc",
                parser=fake_parser,
                state_path=None,
            )

        assert items == expected_items
        mock_page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_parser_returns_nothing(self) -> None:
        scraper = BaseScraper()
        scraper._browser = MagicMock()

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html></html>")

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(scraper, "new_context", new_callable=AsyncMock, return_value=mock_context):
            items = await scraper.scrape_comments(
                url="https://www.threads.com/@alice/post/abc",
                post_pk="abc",
                parser=lambda html, *, post_pk: [],
                state_path=None,
            )

        assert items == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_scraper_comments.py -v
```

Expected: `AttributeError: 'BaseScraper' object has no attribute 'scrape_comments'`

- [ ] **Step 3: Implement `scrape_comments()` in scraper.py**

Add a `Protocol` to `src/horus/core/scraper.py` (after imports, before the class) to describe the keyword-only signature, and add `from typing import Protocol` to imports:

```python
class CommentParser(Protocol):
    def __call__(self, html: str, *, post_pk: str) -> list[ScrapedItem]: ...
```

Add to `BaseScraper` class, after `scrape_page()`:

```python
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
```

Add `Protocol` to the `typing` import in `scraper.py`:

```python
from typing import Any, Protocol
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_scraper_comments.py -v
```

Expected: Both tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/horus/core/scraper.py tests/test_scraper_comments.py
git commit -m "feat(scraper): add scrape_comments() for SSR HTML comment extraction

Co-Authored-By: xero7689 <shzlee217@gmail.com>"
```

---

## Chunk 3: cli.py — --with-comments flag

### Task 3: Wire `--with-comments` into `horus crawl threads`

**Files:**
- Modify: `src/horus/cli.py`
- Test: `tests/test_cli_comments.py` (new file)

When `--with-comments` is present in `extra_args`, after crawling posts, for each saved post (where `is_reply is False`), call `scraper.scrape_comments()` and store the returned items.

The `with_comments` flag is already parsed by `_parse_extra_args()` as `{"with_comments": "true"}` — no changes needed to the parser.

- [ ] **Step 1: Write failing test**

Create `tests/test_cli_comments.py`:

```python
"""Integration-level unit test: crawl with --with-comments triggers comment fetch."""
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, UTC

import pytest
from click.testing import CliRunner

from horus.cli import main
from horus.models import ScrapedItem


def _post(pk: str, is_reply: bool = False) -> ScrapedItem:
    return ScrapedItem(
        id=pk,
        site_id="threads",
        url=f"https://www.threads.net/@alice/post/{pk}",
        text="post text",
        author_name="alice",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        extra={"is_reply": is_reply, "parent_post_id": None, "conversation_id": None},
    )


class TestCrawlWithComments:
    def test_with_comments_flag_triggers_comment_scrape(self, tmp_path) -> None:
        """When --with-comments is passed, scrape_comments() is called for each non-reply post."""
        post1 = _post("p1")
        post2 = _post("p2")
        comment1 = _post("c1", is_reply=True)

        scrape_comments_calls: list[dict] = []

        async def fake_scrape(url, response_filter, parser, state_path, *, since=None, on_progress=None, on_batch=None):
            if on_batch:
                on_batch([post1, post2])
            return [post1, post2]

        async def fake_scrape_comments(url, post_pk, parser, state_path=None):
            scrape_comments_calls.append({"url": url, "post_pk": post_pk})
            return [comment1]

        runner = CliRunner()
        with (
            patch("horus.cli.BaseScraper") as MockScraper,
            patch("horus.cli._get_storage") as mock_storage_fn,
            patch("horus.cli._get_settings") as mock_settings_fn,
        ):
            mock_scraper_inst = AsyncMock()
            mock_scraper_inst.__aenter__ = AsyncMock(return_value=mock_scraper_inst)
            mock_scraper_inst.__aexit__ = AsyncMock(return_value=False)
            mock_scraper_inst.scrape = AsyncMock(side_effect=fake_scrape)
            mock_scraper_inst.scrape_comments = AsyncMock(side_effect=fake_scrape_comments)
            MockScraper.return_value = mock_scraper_inst

            mock_storage = MagicMock()
            mock_storage.get_latest_timestamp.return_value = None
            mock_storage.upsert_items.return_value = 1
            mock_storage_fn.return_value = mock_storage

            mock_settings = MagicMock()
            mock_settings.headless = True
            mock_settings.scroll_delay_min = 1.0
            mock_settings.scroll_delay_max = 2.0
            mock_settings.request_jitter = 0.5
            mock_settings.max_pages = 5
            mock_settings.state_path_for.return_value = tmp_path / "state.json"
            mock_settings_fn.return_value = mock_settings

            result = runner.invoke(
                main,
                ["crawl", "threads", "--user", "alice", "--with-comments"],
            )

        # scrape_comments must be called once per non-reply post collected during crawl
        assert len(scrape_comments_calls) == 2, f"Expected 2 calls, got {len(scrape_comments_calls)}. Result: {result.output}"
        post_pks = {c["post_pk"] for c in scrape_comments_calls}
        assert post_pks == {"p1", "p2"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli_comments.py -v
```

Expected: FAIL — `scrape_comments` not called (feature not implemented).

- [ ] **Step 3: Implement `--with-comments` in cli.py**

In `_crawl()` in `src/horus/cli.py`, locate the response-intercept path (the `else` branch under `has_page_mode`).

**Before** the `for url in urls:` loop, add a collection list:

```python
with_comments = kwargs.get("with_comments") == "true"
crawled_top_level: list[ScrapedItem] = []  # populated in on_batch if with_comments
```

**Inside** the existing `on_batch` callback, append non-reply posts when `with_comments`:

```python
def on_batch(batch: list[ScrapedItem]) -> None:
    nonlocal total_found, total_new
    batch = adapter.post_process(batch)
    new_count = storage.upsert_items(batch)
    total_found += len(batch)
    total_new += new_count
    for item in batch:
        _emit(item)
    # Collect top-level posts for comment fetch
    if with_comments:
        crawled_top_level.extend(
            p for p in batch if not p.extra.get("is_reply", False)
        )
```

**After** the `for url in urls:` loop, add comment-fetching:

```python
# Fetch comments for posts collected during this crawl
if with_comments and crawled_top_level:
    from horus.adapters.threads import parse_comments_from_html
    console.print(f"Fetching comments for {len(crawled_top_level)} posts...")
    for post in crawled_top_level:
        try:
            comment_items = await scraper.scrape_comments(
                url=post.url,
                post_pk=post.id,
                parser=parse_comments_from_html,
                state_path=state_path,
            )
            if comment_items:
                new_count = storage.upsert_items(comment_items)
                total_found += len(comment_items)
                total_new += new_count
                console.print(
                    f"  [dim]{post.url}: {len(comment_items)} comments "
                    f"({new_count} new)[/dim]"
                )
        except Exception as e:
            console.print(
                f"  [yellow]Warning: failed to fetch comments for {post.url}: {e}[/yellow]"
            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_cli_comments.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 6: Lint and typecheck**

```bash
uv run ruff check src/ tests/
uv run mypy src/
```

Fix any issues before committing.

- [ ] **Step 7: Commit**

```bash
git add src/horus/cli.py tests/test_cli_comments.py
git commit -m "feat(cli): add --with-comments flag to crawl threads command

Co-Authored-By: xero7689 <shzlee217@gmail.com>"
```

---

## Chunk 4: Acceptance verification & cleanup

### Task 4: Manual smoke test and AC checklist

**Files:**
- Modify: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/shz/Projects/Dev/horus/Backlog/US-001 抓取貼文留言.md` (mark ACs done)
- Modify: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/shz/Projects/Dev/horus/Progress.md` (record what was done)

- [ ] **Step 1: Smoke test with a real post URL**

```bash
horus crawl threads --url "https://www.threads.com/@meow.coder/post/DVmeKU6k-46" --with-comments
```

Expected output: post crawled, then `Fetching comments for 1 posts...`, then comment count lines.

- [ ] **Step 2: Verify comments are in DB**

```bash
horus show --site threads --limit 30
```

Verify items with `is_reply: True` appear, with `parent_post_id` pointing to the post.

- [ ] **Step 3: Test with --user flag**

```bash
horus crawl threads --user @meow.coder --with-comments
```

Expected: All crawled posts get their comments fetched.

- [ ] **Step 4: Mark US-001 ACs complete in Obsidian**

Update `Backlog/US-001 抓取貼文留言.md` — check all ACs, change `status: in-progress` → `status: done`.

- [ ] **Step 5: Update Kanban.md**

Move `US-001` from `## Backlog` to `## Done`.

- [ ] **Step 6: Update Progress.md**

Append entry:

```markdown
## 2026-03-12 — US-001 抓取貼文留言

實作 `--with-comments` flag for `horus crawl threads`：
- `parse_comments_from_html()` in `threads.py`：從 SSR HTML 解析 thread_items arrays
- `scrape_comments()` in `scraper.py`：導航到貼文頁取得 HTML
- `cli.py`：偵測 `--with-comments` flag，對每篇非 reply 貼文追抓留言
- 留言存入 items 表，`is_reply=True`，`parent_post_id` 正確指向上層
```

- [ ] **Step 7: Final commit (Obsidian notes only — no code changes in this chunk)**

```bash
git add "docs/superpowers/plans/2026-03-12-threads-comments.md"
git commit -m "docs: mark US-001 complete, update progress notes

Co-Authored-By: xero7689 <shzlee217@gmail.com>"
```
