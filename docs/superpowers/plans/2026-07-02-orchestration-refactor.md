# Orchestration Layer Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the crawl orchestration (currently duplicated between `cli.py:_crawl` and `serve/crawler_manager.py:_run`, with the serve copy already drifted/broken in 4 places) into a single `CrawlOrchestrator`, sink Instagram-specific comment logic into the adapter, and consolidate small duplications (stdin reading, `parse_since`).

**Architecture:** A new `core/orchestrator.py` owns the three-way mode dispatch (http / page / intercept) and all storage side-effects. CLI and serve become thin shells that differ only in their `CrawlReporter` implementation (Rich console + stdout JSONL vs SSE broadcast + job log). Adapter-specific comment strategies live in the adapters via a new overridable `SiteAdapter.fetch_comments()` async-generator hook; the `comments_via_interception` flag and all `instagram.*` references in cli.py are deleted.

**Tech Stack:** Python 3.13, Click, Rich, Playwright (via existing `BaseScraper`), pytest. No new dependencies.

**Verification baseline:** `uv run pytest tests/ -q` → 187 passed on `dev` (081169a). Every task must end green. `uv run ruff check src/ tests/` and `uv run ty check src/` must stay green.

**Line-number caveat:** all `file:line` references in this plan are pinned to `dev` @ **081169a**. Tasks 1–3 shift cli.py line numbers; when executing Task 4+, locate blocks by the quoted code, not by the baseline line numbers.

**Known intentional divergences (approved, keep this list in the final PR body):**
1. cli shell's `except (ValueError, RuntimeError)` wraps the whole `run()` call, not just the http-mode `fetch_items()` as today (cli.py:217–222) — a ValueError raised inside page/intercept paths changes from traceback-crash to red-message + exit 1. Acceptable UX improvement.
2. `read_stdin_lines(skip_comments=True)` strips lines **before** the `#` check; the old facebook/web code checked `startswith("#")` on the unstripped line, so `"  # x"` used to pass through as content. The new behavior (drop it) is the intended one.
3. Serve loses two realtime signals (see Task 7): per-page `progress {"fetched","new"}` broadcasts, and mid-crawl `items_found` updates (now written once at the end). The per-URL `log` events still stream, so the UI stays live.

**Ground rules (from the architecture assessment):**

- Tasks 4–7 must be **pure moves / mechanical transforms** wherever possible. Do NOT "fix" suspicious behavior mid-move. Known-suspicious-but-preserved behaviors:
  - `log_crawl` per-URL records **cumulative** totals, not per-URL totals (cli.py:324).
  - stdout JSONL is emitted per item **inside** `on_batch` (pipe contract — timing must not change).
- Do not touch: adapter `parse_response`/parser pure functions, `storage.py`/DB schema, `scrape()` scroll/since semantics in `scraper.py`, CLI flags or stdout JSONL format.
- One intentional generalization (Task 6): the IG-only `is_reply` primary-batch guard becomes adapter-agnostic — drop items with `is_reply=True` **and** `url==""` from primary intercept batches. Behavior-identical for threads/twitter (their replies carry URLs) and for IG (comments have `url=""` until backfilled).

**File map (end state):**

| File | Change |
|---|---|
| `src/horus/adapters/_io.py` | **Create** — `read_stdin_lines()`, `stable_item_id()` |
| `src/horus/core/dates.py` | **Create** — `parse_since()` (raises `ValueError`) |
| `src/horus/core/orchestrator.py` | **Create** — `CrawlReporter` protocol, `CrawlOutcome`, `CrawlOrchestrator` |
| `src/horus/adapters/base.py` | Contract cleanup: abstract→default methods, page-mode hooks declared, `fetch_comments()` hook, delete `comments_via_interception` |
| `src/horus/adapters/instagram.py` | `fetch_comments()` override (grid modal walk moves here) |
| `src/horus/adapters/{threads,twitter,facebook,web,serper,ddg}.py` | Use `_io` helpers; delete dead stubs |
| `src/horus/cli.py` | `_crawl` → thin shell (~60 lines) + `ConsoleReporter` |
| `src/horus/serve/crawler_manager.py` | `_run` → thin shell + `JobReporter`; ~80 duplicate lines deleted |
| `tests/adapters/test_io.py` | **Create** |
| `tests/core/test_dates.py` | **Create** |
| `tests/core/test_orchestrator.py` | **Create** |

---

## Chunk 1: Quick wins (Tasks 1–3, independent of orchestrator)

### Task 1: `adapters/_io.py` — stdin + stable-id helpers

Seven near-identical stdin blocks exist today: threads.py:249–257, twitter.py:293–299, instagram.py:181–187 (the `@user` family), facebook.py:69–77, web.py:49–57 (URL-line family with `#` comment skip), serper.py:113–125, ddg.py:71–83 (`_read_queries`, verbatim twins). `_make_item_id` is duplicated verbatim in serper.py:30–33 and ddg.py:36–39.

**Files:**
- Create: `src/horus/adapters/_io.py`
- Test: `tests/adapters/test_io.py`
- Modify: `src/horus/adapters/threads.py`, `twitter.py`, `instagram.py`, `facebook.py`, `web.py`, `serper.py`, `ddg.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/adapters/test_io.py
"""Tests for shared adapter I/O helpers."""

import io
from typing import Any

from horus.adapters._io import read_stdin_lines, stable_item_id


class _FakeStdin(io.StringIO):
    def isatty(self) -> bool:  # StringIO.isatty() returns False already; be explicit
        return False


def test_read_stdin_lines_basic(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("  a \n\nb\n"))
    assert read_stdin_lines() == ["a", "b"]


def test_read_stdin_lines_skip_comments(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("a\n# comment\nb\n"))
    assert read_stdin_lines(skip_comments=True) == ["a", "b"]
    # without the flag, comment lines pass through (the @user family keeps them)
    monkeypatch.setattr("sys.stdin", _FakeStdin("a\n# comment\n"))
    assert read_stdin_lines() == ["a", "# comment"]


def test_read_stdin_lines_tty_returns_empty(monkeypatch: Any) -> None:
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _Tty("ignored\n"))
    assert read_stdin_lines() == []


def test_read_stdin_lines_oserror_returns_empty(monkeypatch: Any) -> None:
    class _Broken:
        def isatty(self) -> bool:
            return False

        def __iter__(self) -> Any:
            raise OSError("closed")

    monkeypatch.setattr("sys.stdin", _Broken())
    assert read_stdin_lines() == []


def test_stable_item_id_is_deterministic_16_hex() -> None:
    a = stable_item_id("https://x.com/1", "query")
    assert a == stable_item_id("https://x.com/1", "query")
    assert len(a) == 16
    assert a != stable_item_id("https://x.com/1", "other")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/adapters/test_io.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'horus.adapters._io'`

- [ ] **Step 3: Implement `_io.py`**

```python
# src/horus/adapters/_io.py
"""Shared I/O helpers for adapters.

Plain utility functions — adapters opt in by importing; nothing here is part of
the SiteAdapter contract, so adapter independence is preserved.
"""

import hashlib
import sys


def read_stdin_lines(*, skip_comments: bool = False) -> list[str]:
    """Read non-empty stripped lines from a piped stdin.

    Returns [] when stdin is a TTY (interactive) or unreadable — callers fall
    through to their own "missing required option" error.
    """
    if not (hasattr(sys.stdin, "isatty") and not sys.stdin.isatty()):
        return []
    try:
        lines = [line.strip() for line in sys.stdin if line.strip()]
    except OSError:
        return []
    if skip_comments:
        lines = [ln for ln in lines if not ln.startswith("#")]
    return lines


def stable_item_id(url: str, query: str) -> str:
    """Stable ID: SHA-1 of (url, query) truncated to 16 hex chars."""
    digest = hashlib.sha1(f"{url}|{query}".encode()).hexdigest()  # nosec B324
    return digest[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/adapters/test_io.py -q`
Expected: 5 passed

- [ ] **Step 5: Migrate the 7 call sites (mechanical, one adapter at a time, suite green after each)**

Each replacement removes the local `hasattr(sys.stdin, ...)` block. Exact rewrites:

`threads.py` (get_urls tail — replaces lines 249–258):
```python
        from horus.adapters._io import read_stdin_lines  # (move to module imports)

        usernames = [ln.lstrip("@") for ln in read_stdin_lines()]
        if usernames:
            if mode == "replies":
                return [f"https://www.threads.net/@{u}/replies" for u in usernames]
            return [f"https://www.threads.net/@{u}" for u in usernames]
        raise ValueError("threads adapter requires --user or --url")
```

`twitter.py` (293–299): `usernames = [ln.lstrip("@") for ln in read_stdin_lines()]`, keep `self._profile_url(u, mode)` mapping.

`instagram.py` (181–187): `users = [ln.lstrip("@") for ln in read_stdin_lines()]`.

`facebook.py` (69–77) and `web.py` (49–57): `lines = read_stdin_lines(skip_comments=True)`; `if lines: return lines`.

`serper.py` and `ddg.py`: in `_read_queries`, replace **only the stdin block** with `lines = read_stdin_lines()` — the `--query` kwarg precedence and the trailing `raise ValueError(...)` stay exactly as they are:
```python
    def _read_queries(self, **kwargs: Any) -> list[str]:
        query: str | None = kwargs.get("query")
        if query:
            return [query]
        lines = read_stdin_lines()
        if lines:
            return lines
        raise ValueError("serper adapter requires --query")   # ddg: "ddg adapter requires --query"
```
Also delete both local `_make_item_id` functions and import `stable_item_id` from `horus.adapters._io` (keep call sites' argument order `(url, query)`).

Note: `import sys` becomes unused in adapters that only used it for stdin — remove it (ruff F401 will flag).

- [ ] **Step 6: Full suite + lint**

Run: `uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ty check src/`
Expected: 192 passed (187 + 5 new), ruff/ty green

- [ ] **Step 7: Commit**

```bash
git add src/horus/adapters/ tests/adapters/test_io.py
git commit -m "refactor(adapters): consolidate stdin reading and stable-id into _io helpers"
```

### Task 2: `core/dates.py` — single `parse_since`

Three implementations exist with divergent semantics: cli.py:34–40 (raises `click.BadParameter`), crawler_manager.py:177–186 (silent `pass`), serve/routes/items.py:31–35 (silent `pass`). One parser, callers choose the failure UX.

**Files:**
- Create: `src/horus/core/dates.py`
- Test: `tests/core/test_dates.py` — **also create `tests/core/__init__.py`** (the repo convention: `tests/__init__.py`, `tests/adapters/__init__.py`, `tests/serve/__init__.py` all exist)
- Modify: `src/horus/cli.py:34-40`, `src/horus/serve/crawler_manager.py:177-186`, `src/horus/serve/routes/items.py:31-35`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_dates.py
from datetime import UTC, datetime

import pytest

from horus.core.dates import parse_since


def test_parse_since_none_and_empty() -> None:
    assert parse_since(None) is None
    assert parse_since("") is None


def test_parse_since_date_and_iso() -> None:
    assert parse_since("2026-01-02") == datetime(2026, 1, 2, tzinfo=UTC)
    assert parse_since("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_since_invalid_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        parse_since("not-a-date")
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/core/test_dates.py -q` → ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# src/horus/core/dates.py
"""Date parsing shared by CLI and serve."""

from datetime import UTC, datetime


def parse_since(since_str: str | None) -> datetime | None:
    """Parse a --since string (YYYY-MM-DD or ISO 8601) to an aware UTC datetime.

    Returns None for None/empty. Raises ValueError on malformed input —
    callers decide the failure UX (CLI: BadParameter; serve: fall back to None).
    """
    if not since_str:
        return None
    return datetime.fromisoformat(since_str).replace(tzinfo=UTC)
```

- [ ] **Step 4: Migrate the 3 call sites**

cli.py — `_parse_since` becomes a thin wrapper (keeps `click.BadParameter` UX and the existing call sites in `_crawl`/`show`):
```python
from horus.core.dates import parse_since

def _parse_since(since_str: str | None) -> datetime | None:
    try:
        return parse_since(since_str)
    except ValueError:
        raise click.BadParameter(f"Invalid date format: '{since_str}'. Use YYYY-MM-DD or ISO 8601.")
```

crawler_manager.py:177–186 → (deletes the local `from datetime import datetime as dt` import):
```python
                since_dt = None
                if since:
                    try:
                        since_dt = parse_since(since)
                    except ValueError:
                        pass  # serve keeps silent-fallback semantics (documented divergence)
```

serve/routes/items.py:31–35 → same try/except ValueError shape around `parse_since(since)`.

- [ ] **Step 5: Full suite + lint** — `uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run ty check src/` → 195 passed, green

- [ ] **Step 6: Commit** — `git commit -m "refactor(core): single parse_since shared by cli and serve"`

### Task 3: `base.py` contract cleanup (ISP + declare page-mode hooks)

Today 4 adapters stub dead abstract methods (web.py:26–32, facebook.py:57–63, serper.py:54–61, ddg.py:61–69), and the page-mode hooks (`wait_for_selector`, `page_script`, `content_script`) are an undeclared protocol accessed via `getattr` (cli.py:234–236) — the root cause of serve's page-mode drift.

**Files:**
- Modify: `src/horus/adapters/base.py`, `web.py`, `facebook.py`, `serper.py`, `ddg.py`, `src/horus/cli.py:232-246`
- Test: existing suite is the guard; add one contract test to `tests/adapters/test_base_contract.py`

- [ ] **Step 1: Write failing contract test**

```python
# tests/adapters/test_base_contract.py
"""Locks the SiteAdapter contract: page/http adapters need no interception stubs."""

from collections.abc import Callable
from typing import Any

from horus.adapters.base import SiteAdapter


class _PageOnly(SiteAdapter):
    site_id = "page_only"
    display_name = "Page Only"
    login_url = "https://example.com/login"
    requires_login = False
    has_page_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return ["https://example.com"]


def test_page_adapter_needs_no_interception_stubs() -> None:
    a = _PageOnly()  # must instantiate without overriding interception methods
    assert a.parse_response({}) == []
    f: Callable[[str, dict[str, Any]], bool] = a.get_response_filter()
    assert f("https://example.com", {}) is False


def test_page_mode_hooks_declared_with_none_defaults() -> None:
    assert SiteAdapter.wait_for_selector is None
    assert SiteAdapter.page_script is None
    assert SiteAdapter.content_script is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/adapters/test_base_contract.py -q` → TypeError (abstract methods) / AttributeError (hooks)

- [ ] **Step 3: Modify `base.py`**

- Demote `get_response_filter` and `parse_response` from `@abstractmethod` to default implementations (`get_urls` **stays abstract** — every adapter implements it meaningfully):

```python
    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        """(response_url, response_body) -> bool. Override in interception adapters."""
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        """Parse an intercepted response body. Override in interception adapters."""
        return []
```

- Declare the page-mode hooks as formal ClassVars (below the existing mode flags):

```python
    # Page-mode hooks (used only when has_page_mode=True); declared here so callers
    # never need getattr — undeclared hooks are how serve drifted from cli.
    wait_for_selector: ClassVar[str | None] = None
    page_script: ClassVar[str | None] = None
    content_script: ClassVar[str | None] = None
```

- [ ] **Step 4: Delete the dead stubs**

Remove `get_response_filter`/`parse_response` overrides from `web.py`, `facebook.py`, `serper.py`, `ddg.py` (they are now inherited). Remove the now-unused `Callable`/`ScrapedItem` imports where ruff flags them. facebook.py's real hook assignments (`wait_for_selector`, `page_script`, `content_script` ClassVars) stay as-is — they now override declared base attributes.

- [ ] **Step 5: Replace `getattr` in cli.py:234–236 with direct attribute access**

```python
                    wait_for = adapter_cls.wait_for_selector
                    page_script = adapter_cls.page_script
                    content_script = adapter_cls.content_script
```

- [ ] **Step 6: Full suite + lint** — expected 197 passed, green

- [ ] **Step 7: Commit** — `git commit -m "refactor(adapters): demote interception methods to defaults, declare page-mode hooks in contract"`

---

## Chunk 2: Orchestrator pure move (Task 4)

### Task 4: Create `core/orchestrator.py` — move `_crawl`'s body verbatim

**This is a pure move.** The `_crawl` body (cli.py:158–429 minus the adapter/URL resolution which stays in cli) moves into `CrawlOrchestrator.run()`. Zero logic changes; console output goes through an injected reporter whose CLI implementation reproduces today's exact strings; `BaseScraper` is injected as a class so the existing `patch("horus.cli.BaseScraper")` in ~10 test files keeps working unchanged.

**Files:**
- Create: `src/horus/core/orchestrator.py`
- Modify: `src/horus/cli.py` (`_crawl` becomes a shell)
- Test: `tests/core/test_orchestrator.py` (smoke only in this task; existing CLI tests are the real guard)

- [ ] **Step 1: Write the orchestrator scaffolding (types + constructor)**

```python
# src/horus/core/orchestrator.py
"""Single source of truth for crawl orchestration, shared by cli and serve.

The three-way mode dispatch (http / page / intercept), storage side-effects and
comment fetching live here. Frontends differ only in their CrawlReporter.
"""

import asyncio
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
```

- [ ] **Step 2: Move the `_crawl` body into `run()`**

Signature (all inputs resolved by the caller — orchestrator does **no** Click/adapter-registry errors that `sys.exit`):

```python
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
        cls = adapter_cls if adapter_cls is not None else type(adapter)
        outcome = outcome if outcome is not None else CrawlOutcome()
```

Two signature decisions with test-compat rationale — **do not simplify them away**:

- **`adapter_cls` is a separate parameter.** `tests/test_cli_comments.py` builds the adapter as a bare `MagicMock()` *instance* while the mode flags live on a separately-patched mock *class* — today cli reads flags from `adapter_cls` (cli.py:210/232/268), and `type(MagicMock()).has_http_mode` raises `AttributeError`. The orchestrator must keep reading mode flags from `cls` (`cls.has_http_mode`, `cls.has_page_mode`, `getattr(cls, "comments_via_interception", False)`), and cli passes the `get_adapter()` return value as `adapter_cls`.
- **`outcome` is caller-owned and exceptions propagate.** The orchestrator catches **nothing** — no `KeyboardInterrupt`, no `asyncio.CancelledError`. It mutates the passed-in `outcome` as it goes. The CLI shell creates the outcome, catches `(KeyboardInterrupt, asyncio.CancelledError)` itself and prints the Interrupted summary from the partially-filled outcome. Serve (Task 7) relies on `CancelledError` propagating to its own handler (crawler_manager.py:220–223) to set `CANCELLED` — if the orchestrator swallowed it, a cancelled job would finish as `DONE`.

Move rules (verbatim except these mechanical substitutions):

| cli.py source | becomes |
|---|---|
| `console.print(...)` | `self._reporter.log(...)` — **strip Rich markup is NOT allowed**; keep markup strings identical, ConsoleReporter passes them to `console.print` |
| `_emit(item)` closure | `self._reporter.emit(item)` |
| `on_progress` closure body | `self._reporter.progress(scroll, total)` |
| `storage.` | `self._storage.` |
| `BaseScraper(**scraper_kwargs)` | `self._scraper_cls(**scraper_kwargs)` |
| `since = _parse_since(since_str); if since is None:` | `if since is None:` (caller already parsed; latest-timestamp fallback stays inside `run()`) |
| http-mode error path: `console.print(f"[red]{e}[/red]"); storage.close(); sys.exit(1)` | `raise` — the caller owns process exit. The catch `(ValueError, RuntimeError)` is **removed**; exceptions propagate |
| `except (KeyboardInterrupt, asyncio.CancelledError): interrupted = True` | **deleted from the orchestrator** — moves to the CLI shell (see the caller-owned `outcome` rationale above). `run()` mutates the caller's outcome and lets exceptions fly |
| final `storage.close()` | **removed** — the caller created storage, the caller closes it |
| `if output_dir: _write_page_md(page, output_dir)` | `if output_dir and write_page_md: write_page_md(page, output_dir)` (function injected to avoid orchestrator→cli import) |

Everything else — the `scraper_kwargs` dict, the `on_batch` closure with its `is_reply` guard and `nonlocal` totals (now `outcome.total_found` etc. via a small local mutable), `crawled_via_post_url`, the SSR `is_post_url` branch, the whole `with_comments` block including the Instagram-specific branch **with its current comments and local `from horus.adapters.instagram import ...` import** — moves as-is. Flattening comes in Task 5; do not restructure here.

- [ ] **Step 3: Rewrite cli.py `_crawl` as a shell**

Keeps: settings/storage creation, adapter resolution + `sys.exit(1)` error UX, `_parse_since` (BadParameter UX), state-path warning, output-dir mkdir, final summary print, `storage.close()`. Delegates the rest:

```python
class _ConsoleReporter:
    def __init__(self, emit_stdout: bool) -> None:
        self._emit_stdout = emit_stdout

    def log(self, message: str) -> None:
        console.print(message)

    def progress(self, scroll: int, total: int) -> None:
        console.print(f"  [dim]scroll #{scroll} — {total} items so far[/dim]")

    def emit(self, item: ScrapedItem | ScrapedPage) -> None:
        if self._emit_stdout:
            click.echo(item.model_dump_json())


async def _crawl(...同簽名...) -> None:
    settings = _get_settings()
    storage = _get_storage(settings)
    try:
        adapter_cls = get_adapter(site)
    except ValueError as e:
        console.print(f"[red]{e}[/red]"); storage.close(); sys.exit(1)
    adapter = adapter_cls()
    kwargs = _parse_extra_args(extra_args)
    try:
        urls = adapter.get_urls(**kwargs)
    except ValueError as e:
        console.print(f"[red]{e}[/red]"); storage.close(); sys.exit(1)
    output_dir = Path(output) if output else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    state_path = None if no_state else settings.state_path_for(site)
    if state_path and not state_path.exists():
        if adapter.requires_login:
            console.print(f"[yellow]Warning:[/yellow] No login state found at {state_path}. "
                          f"Run [bold]horus login {site}[/bold] first.")
        state_path = None
    orch = CrawlOrchestrator(
        storage, settings,
        scraper_cls=BaseScraper,   # ← name resolved at call time; patch("horus.cli.BaseScraper") still works
        reporter=_ConsoleReporter(emit_stdout),
    )
    # Parse --since ONLY for the intercept path, exactly as today: cli.py:263 reaches
    # _parse_since only inside the intercept branch, so `crawl web --since garbage`
    # is currently ignored — parsing eagerly for all modes would turn it into a
    # BadParameter failure (behavior change; codex review catch).
    since = (
        _parse_since(since_str)
        if not (adapter_cls.has_http_mode or adapter_cls.has_page_mode)
        else None
    )
    outcome = CrawlOutcome()
    try:
        await orch.run(
            adapter, urls, adapter_cls=adapter_cls, site=site, kwargs=kwargs, limit=limit,
            since=since, state_path=state_path,
            output_dir=output_dir, write_page_md=_write_page_md, outcome=outcome,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        outcome.interrupted = True
    except (ValueError, RuntimeError) as e:   # was the http-mode sys.exit path (known divergence #1)
        console.print(f"[red]{e}[/red]"); storage.close(); sys.exit(1)
    if outcome.interrupted:
        console.print(f"\n[yellow]Interrupted.[/yellow] {outcome.total_found} fetched, {outcome.total_new} saved.")
    else:
        console.print(f"\n[bold]Done.[/bold] {outcome.total_found} fetched, {outcome.total_new} new saved.")
    storage.close()
```

**Critical patch-compat check:** the existing CLI tests patch `horus.cli.BaseScraper` (2 files, 7 patch sites: `tests/adapters/test_instagram_cli.py`, `tests/test_cli_comments.py`). Because `_crawl` passes the module-level name `BaseScraper` at call time, the patched mock flows into the orchestrator. Verify by running the whole suite BEFORE writing any new tests.

- [ ] **Step 4: Run full suite — the real gate for the pure move**

Run: `uv run pytest tests/ -q`
Expected: 197 passed with **zero test modifications**. If any CLI test fails, the move introduced a behavior change — fix the move, not the test.

- [ ] **Step 5: Add orchestrator smoke test**

```python
# tests/core/test_orchestrator.py
"""Smoke: orchestrator http-mode path with a stub adapter and NullReporter."""

import asyncio
from typing import Any

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.orchestrator import CrawlOrchestrator
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem
from datetime import UTC, datetime


class _HttpStub(SiteAdapter):
    site_id = "http_stub"
    display_name = "HTTP Stub"
    login_url = ""
    requires_login = False
    has_http_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        assert kwargs["limit"] == 7  # top-level limit forwarded
        return [ScrapedItem(id="X1", site_id="http_stub", url="https://e.com/1",
                            timestamp=datetime(2024, 1, 1, tzinfo=UTC))]


def test_http_mode_forwards_limit_and_persists(tmp_path: Any) -> None:
    storage = HorusStorage(tmp_path / "t.db")
    orch = CrawlOrchestrator(storage, Settings())
    outcome = asyncio.run(orch.run(
        _HttpStub(), [], site="http_stub", kwargs={}, limit=7,
        since=None, state_path=None,
    ))
    assert (outcome.total_found, outcome.total_new) == (1, 1)
    assert storage.get_items(site_id="http_stub", limit=10)[0].id == "X1"
    storage.close()
```

- [ ] **Step 6: Full suite + lint** — 198 passed, green

- [ ] **Step 7: Commit** — `git commit -m "refactor(core): extract CrawlOrchestrator from cli._crawl (pure move)"`

---

## Chunk 3: Flatten + sink comments into adapters (Tasks 5–6)

### Task 5: Flatten the three-way dispatch inside the orchestrator

Mechanical split of `run()` into `_run_http` / `_run_pages` / `_run_intercept` private methods; the nested closures (`on_batch`, `_upsert_comments`) become methods or explicit local state. No behavior change; existing suite is the gate.

**Files:** Modify `src/horus/core/orchestrator.py`

- [ ] **Step 1:** `run()` keeps only: `cls`/`outcome` resolution (Task 4 signature), `scraper_kwargs` assembly, and the dispatch. No exception handling (caller-owned, per Task 4):

```python
        cls = adapter_cls if adapter_cls is not None else type(adapter)
        outcome = outcome if outcome is not None else CrawlOutcome()
        if cls.has_http_mode:
            await self._run_http(adapter, site=site, kwargs=kwargs, limit=limit,
                                 outcome=outcome, started_at=started_at)
        else:
            async with self._scraper_cls(**scraper_kwargs) as scraper:
                if cls.has_page_mode:
                    await self._run_pages(adapter, scraper, urls, ...)
                else:
                    await self._run_intercept(adapter, cls, scraper, urls, ...)
        return outcome
```

(`_run_intercept` receives `cls` because the `comments_via_interception` flag read — until Task 6 deletes it — must stay `getattr(cls, "comments_via_interception", False)` for MagicMock-class compat.)

- [ ] **Step 2:** `_run_intercept` hosts the former closures. `nonlocal total_found` state becomes writes to `outcome.total_found` (dataclass is mutable — closures inside the method may capture `outcome` directly).
- [ ] **Step 3:** Run full suite after each extracted method, not only at the end.
- [ ] **Step 4:** Verify complexity dropped: `uv run ruff check src/horus/core/orchestrator.py --select C901,PLR0912,PLR0915 --no-cache` — expect **no C901 > 15** (the intercept path keeps inherent complexity; target ≤ 15 each, do not force-split further).
- [ ] **Step 5:** Full suite + lint → green. Commit: `git commit -m "refactor(core): flatten orchestrator dispatch into per-mode methods"`

### Task 6: `SiteAdapter.fetch_comments()` — sink the IG grid walk into the adapter

Deletes: `comments_via_interception` flag, the Instagram import inside the orchestrator, the hardcoded `instagram.com` URL, and the IG-specific `use_intercept` branch. The orchestrator becomes adapter-agnostic: `async for post, comments in adapter.fetch_comments(...)`.

**Files:**
- Modify: `src/horus/adapters/base.py`, `src/horus/adapters/instagram.py`, `src/horus/core/orchestrator.py`
- Tests: `tests/adapters/test_instagram_cli.py` (stub adapters lose the flag, gain overrides), `tests/adapters/test_instagram_adapter.py` (flag test → fetch_comments test), `tests/test_cli_comments.py` (**must be updated** — see Step 5; its adapter is a bare `MagicMock`, and a MagicMock child mock's `__aiter__` iterates zero times, so `async for ... in adapter.fetch_comments(...)` would silently skip the walk and the `scrape_comments` call-count asserts would fail)

- [ ] **Step 1: Write the failing adapter-level test for the default (HTML) strategy**

```python
# append to tests/adapters/test_base_contract.py
# (add to the file's imports: asyncio, AsyncMock/MagicMock from unittest.mock,
#  ScrapedItem from horus.models, UTC/datetime from datetime)
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from horus.models import ScrapedItem


def test_default_fetch_comments_uses_comment_parser() -> None:
    """Default strategy: per-post scrape_comments() with get_comment_parser()."""

    class _WithParser(_PageOnly):
        site_id = "with_parser"

        def get_comment_parser(self):  # type: ignore[override]
            return lambda html, *, post_pk: []

    a = _WithParser()
    post = ScrapedItem(id="P1", site_id="with_parser", url="https://e.com/p/1",
                       timestamp=datetime(2024, 1, 1, tzinfo=UTC))
    scraper = MagicMock()
    scraper.scrape_comments = AsyncMock(return_value=[])

    async def collect():
        return [pair async for pair in a.fetch_comments(scraper, [post], None)]

    pairs = asyncio.run(collect())
    assert pairs == [(post, [])]
    scraper.scrape_comments.assert_awaited_once()


def test_default_fetch_comments_without_parser_yields_empty() -> None:
    a = _PageOnly()  # get_comment_parser() -> None
    scraper = MagicMock()

    async def collect():
        return [pair async for pair in a.fetch_comments(scraper, [], None)]

    assert asyncio.run(collect()) == []
```

- [ ] **Step 2: Implement the base hook**

```python
# base.py — replaces the comments_via_interception flag (DELETE the flag line)
    async def fetch_comments(
        self,
        scraper: "BaseScraper",
        posts: list[ScrapedItem],
        state_path: "Path | None",
        *,
        via_post_url: bool = False,
        on_event: Callable[[str], None] | None = None,
    ) -> "AsyncIterator[tuple[ScrapedItem, list[ScrapedItem]]]":
        """Yield (post, comments) pairs for --with-comments.

        Default strategy: per-post HTML navigation via get_comment_parser()
        (threads). Adapters with bespoke choreography (e.g. Instagram's
        profile-grid modal walk) override this. Errors for one post must not
        abort the rest — report via on_event and continue.

        Contract: implementations only FETCH and ATTRIBUTE comments (including
        url backfill). They must not persist, emit, or post_process — the
        orchestrator owns all storage/reporting side effects.
        """
        parser = self.get_comment_parser()
        for post in posts:
            try:
                comments = (
                    await scraper.scrape_comments(
                        url=post.url, post_pk=post.id, parser=parser, state_path=state_path
                    )
                    if parser
                    else []
                )
            except Exception as e:  # noqa: BLE001 — one bad post must not kill the walk
                if on_event:
                    on_event(f"  [yellow]Warning: failed to fetch comments for {post.url}: {e}[/yellow]")
                continue
            yield post, comments
```

(Type imports: `AsyncIterator` from `collections.abc`, `Path` under `TYPE_CHECKING` alongside the existing `CommentParser` import.)

- [ ] **Step 3: Override in `instagram.py` — move the grid-walk block (orchestrator's former cli.py:343–401) verbatim**

```python
    async def fetch_comments(self, scraper, posts, state_path, *, via_post_url=False, on_event=None):
        # IG comments only fire when a post is opened as an in-app MODAL from the
        # profile grid. Walk each author's grid once; attribute by media_id == post pk.
        posts_by_profile: dict[str, list[ScrapedItem]] = {}
        for post in posts:
            handle = (post.author_name or "").lstrip("@")
            if handle:
                posts_by_profile.setdefault(handle, []).append(post)

        for handle, profile_posts in posts_by_profile.items():
            profile_url = f"https://www.instagram.com/{handle}/"
            target_codes = (
                [c for p in profile_posts if (c := post_code_from_url(p.url))]
                if via_post_url
                else None
            )
            try:
                raw = await scraper.scrape_comments_via_grid(
                    profile_url,
                    self.get_response_filter(),
                    self.parse_response,
                    state_path,
                    max_posts=len(profile_posts),
                    target_codes=target_codes,
                    on_progress=(lambda done, total: on_event(
                        f"  [dim]modal {done} — {total} responses[/dim]")) if on_event else None,
                )
            except Exception as e:  # noqa: BLE001
                if on_event:
                    on_event(f"  [yellow]Warning: comment walk failed for @{handle}: {e}[/yellow]")
                continue
            comments_by_media: dict[str, list[ScrapedItem]] = {}
            for c in raw:
                if c.extra.get("is_reply"):
                    comments_by_media.setdefault(c.extra.get("parent_post_id", ""), []).append(c)
            for post in profile_posts:
                cs = comments_by_media.get(post.id, [])
                for c in cs:
                    c.url = post.url  # backfill (comment body lacks shortcode)
                yield post, cs
```

Note the one intentional divergence: the old code called `_upsert_comments` only for matching posts; yielding `(post, [])` for posts with no comments is equivalent (`_upsert_comments` early-returns on empty) — the orchestrator's consumer must keep that early-return (skip upsert/report when `comments == []`).

- [ ] **Step 4: Rewire the orchestrator's `--with-comments` block**

Replace the whole `if use_intercept: ... else: ...` fork with:

```python
        if with_comments and crawled_top_level:
            self._reporter.log(f"Fetching comments for {len(crawled_top_level)} posts...")
            async for post, comment_items in adapter.fetch_comments(
                scraper, crawled_top_level, state_path,
                via_post_url=crawled_via_post_url, on_event=self._reporter.log,
            ):
                if not comment_items:
                    continue
                new_count = self._storage.upsert_items(comment_items)
                outcome.total_found += len(comment_items)
                outcome.total_new += new_count
                self._reporter.log(
                    f"  [dim]{post.url}: {len(comment_items)} comments ({new_count} new)[/dim]"
                )
```

And generalize the primary-batch guard (delete the `use_intercept` flag read):

```python
            # URL-less replies are unattributed comments: an intercepted comment has
            # url="" until --with-comments attribution backfills it, and must never
            # persist from a primary batch. (Threads/twitter replies carry URLs and
            # are unaffected.) This guard runs unconditionally — with or without
            # --with-comments — matching the old IG flag guard's semantics.
            batch = [p for p in batch if not (p.extra.get("is_reply") and not p.url)]
```

And the SSR routing condition `if use_intercept and adapter.is_post_url(url):` becomes `if adapter.is_post_url(url):` (base default returns False — behavior identical for threads/twitter/etc.).

- [ ] **Step 5: Update tests that referenced the flag or MagicMock adapters**

- `test_instagram_adapter.py::test_comments_via_interception_flag` → replace with a test asserting `InstagramAdapter.fetch_comments` is overridden (`InstagramAdapter.fetch_comments is not SiteAdapter.fetch_comments`).
- `test_instagram_cli.py`: `_StubIgAdapter` drops `comments_via_interception = True` and **must gain an explicit `fetch_comments` override** — a plain `SiteAdapter` subclass would inherit the default HTML strategy, NOT the grid walk (codex review catch). The override mirrors the IG strategy shape: group by author, call the (still-mocked) `scraper.scrape_comments_via_grid`, attribute by `parent_post_id`, backfill urls, yield `(post, comments)`. Simplest correct option: make `_StubIgAdapter.fetch_comments = InstagramAdapter.fetch_comments` (unbound reuse) or copy the ~20-line override. **Keep all six behavioral invariants intact** (grid-walk used, mixed-batch deferral, foreign-comment rejection, no-with-comments drop, multi-profile attribution, --url target_codes).
- `tests/test_cli_comments.py`: the adapter is a bare `MagicMock()`; bind the real default onto it so the async-generator path exercises `scrape_comments` exactly as before: `mock_adapter.fetch_comments = SiteAdapter.fetch_comments.__get__(mock_adapter)` (plus `mock_adapter.get_comment_parser.return_value = <the parser the test already sets>`). Alternatively convert the mock adapter to a small real `SiteAdapter` stub — either way the existing call-count assertions (`len(scrape_comments_calls) == 2` etc.) must keep passing unmodified.

- [ ] **Step 6: Full suite + lint + complexity re-check** — green; `orchestrator.py` now has zero `instagram` references: `grep -n instagram src/horus/core/orchestrator.py` → empty.

- [ ] **Step 7: Commit** — `git commit -m "refactor(adapters): sink comment strategies into SiteAdapter.fetch_comments, drop comments_via_interception"`

---

## Chunk 4: Serve adoption + finish (Task 7)

### Task 7: `crawler_manager._run` → orchestrator + `JobReporter`

Deletes the ~80-line hand copy. **This task intentionally changes serve behavior — all four changes are drift fixes, list them in the commit body:**

1. Page-mode now passes `wait_for_selector`/`page_script`/`content_script` → Facebook via serve expands「查看更多」again.
2. Intercept path gains the SSR `is_post_url` routing → IG single-post URLs work via serve.
3. Intercept path gains the URL-less-reply guard.
4. `items_found` semantics change from "scrape's running total" (on_progress) to "sum of persisted batches" (same as CLI) — more accurate, may differ slightly from before.
5. **Realtime granularity loss** (known divergence #3): page-mode's per-page `progress {"fetched","new"}` broadcast (crawler_manager.py:163–165) disappears, and `job.items_found`/`items_new` are written once from the outcome at the end instead of live-updated (197–199, 204) — a UI polling `to_dict()` mid-crawl sees 0 until done. The per-URL `log` events still stream via `_JobReporter.log`, and intercept scroll progress still broadcasts via `_JobReporter.progress`.

`--with-comments` via serve: works if a job's kwargs contain `with_comments: "true"`; the serve UI doesn't expose it yet (out of scope).

**Files:**
- Modify: `src/horus/serve/crawler_manager.py`
- Test: existing serve tests (check `tests/` for serve coverage; if none exist for `_run`, add one smoke test with a stubbed orchestrator-compatible adapter)

- [ ] **Step 1:** Write `JobReporter`:

```python
class _JobReporter:
    """Bridges CrawlReporter events onto a CrawlJob (SSE + log)."""

    def __init__(self, job: CrawlJob) -> None:
        self._job = job

    def log(self, message: str) -> None:
        # Strip Rich markup for the web log (serve renders plain text). Use Rich's
        # own parser — a hand-rolled regex misses styles with punctuation/attributes
        # (codex review catch).
        from rich.text import Text

        plain = Text.from_markup(message).plain
        self._job.log.append(plain)
        self._job.broadcast("log", {"message": plain})

    def progress(self, scroll: int, total: int) -> None:
        self._job.broadcast("progress", {"scroll": scroll, "total": total})

    def emit(self, item: Any) -> None: ...  # serve does not stream items
```

- [ ] **Step 2:** `_run` becomes: status/broadcast bookkeeping + storage + since parse (silent fallback) + state-path resolution (reuse the same `state_path.exists()` check **once**, not the current two copies at lines 153–154 and 188–189) + one `orchestrator.run(...)` call + outcome→job field mapping (`job.items_found = outcome.total_found` etc.) + existing exception/CancelledError/finally handling unchanged — the orchestrator re-raises `CancelledError` (Task 4 design), so serve's `except asyncio.CancelledError → CANCELLED → raise` keeps working.

  **`get_urls` trap:** today serve's http branch never calls `adapter.get_urls()` (only page/intercept branches do), and `tests/serve/test_crawler_manager_limit.py`'s `_FakeHttpAdapter` is a plain class **without** `get_urls`. The new shell must preserve this: for `adapter_cls.has_http_mode`, skip `get_urls` and pass `urls=[]` (the orchestrator's http path ignores `urls`). Do NOT mirror cli's unconditional `get_urls` call.
- [ ] **Step 3:** Delete the three per-mode blocks (lines 130–215).
- [ ] **Step 4:** Check existing serve tests before rewiring: `grep -rn "crawler_manager" tests/` — `tests/serve/test_crawler_manager_limit.py` exists (http-mode limit forwarding, fake adapter without `get_urls`). If any test patches `horus.serve.crawler_manager.BaseScraper`, pass `scraper_cls=BaseScraper` from the module namespace exactly like cli does. These serve tests must pass with at most mechanical updates (documented in the commit if any).
- [ ] **Step 5:** Full suite + lint → green. Manual smoke (optional, needs Playwright): `uv run horus serve` + submit a `web` crawl from the UI.
- [ ] **Step 6:** Commit with the four drift fixes listed:

```bash
git commit -m "refactor(serve): route crawl jobs through CrawlOrchestrator" -m "Fixes serve-side drift: page-mode hooks (facebook see-more), IG single-post SSR routing, URL-less reply guard, items_found now counts persisted batches like the CLI."
```

### Task 8: Docs sync + final verification

- [ ] **Step 1:** Update `CLAUDE.md` 專案結構 section: add `core/orchestrator.py`, `core/dates.py`, `adapters/_io.py`; update the Instagram section (flag removed → `fetch_comments()` override); note cli/serve are thin shells.
- [ ] **Step 2:** Update `~/.claude/skills/horus-scraping/SKILL.md` **only if** user-facing behavior changed (serve gains IG --url and facebook fix — add one line).
- [ ] **Step 3:** Final gate:

```bash
uv run pytest tests/ -q                     # all green
uv run ruff check src/ tests/               # green
uv run ty check src/                        # green
uv run ruff check src/ --select C901,PLR0912,PLR0915 --no-cache   # cli.py:_crawl gone from the list
uv run horus list-sites                     # CLI alive
grep -rn "comments_via_interception" src/   # empty
grep -n "instagram" src/horus/core/orchestrator.py src/horus/cli.py   # empty
```

- [ ] **Step 4:** Live regression (needs the saved IG session; ask the user before running — real account):

```bash
uv run horus crawl instagram --url https://www.instagram.com/p/DZtid4YJ4pX/ --with-comments
uv run horus crawl threads --user <a-known-handle> --limit 5   # threads HTML comment path unaffected
```

- [ ] **Step 5:** Commit docs: `git commit -m "docs: sync CLAUDE.md with orchestrator refactor"`
