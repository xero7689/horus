# Serper Search Adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `serper` adapter，讓 `horus crawl serper --query "..."` 透過 Serper.dev 的 Google Search API 取得真・Google 搜尋結果並存進 DB，並附帶一套可量化「結果是否貼近瀏覽器 Google」的驗證工具。

**Architecture:** 沿用 `ddg.py` 的 `has_http_mode=True` 形狀，但改用 `httpx` POST `https://google.serper.dev/search`（header `X-API-KEY`）。金鑰 **env-only**（`HORUS_SERPER_API_KEY`，已放進 `.env`），絕不經 CLI flag，避免落入 `crawl_log` 與 serve API。額外加一個純函式模組 `serp_eval.py`（overlap@k + RBO + URL 正規化）做結果品質驗證。

**Tech Stack:** Python 3.13, httpx（移到 runtime deps）, pydantic-settings, Click, pytest + `httpx.MockTransport`（CI 無真實 network call）。

---

## 取代說明（Supersedes）

本 plan **取代** `docs/superpowers/plans/2026-05-14-google-search-adapter.md` 的 Google Custom Search JSON API 方向。原因：Custom Search JSON API 已對新客戶關閉、2027-01-01 sunset，Step 0 gate 過不去。改用 Serper.dev（已完成註冊、金鑰實測可用、`curl` 回 200 + 1 credit）。

與原 Google plan 的**差異點**：

| 項目 | Google CSE plan（舊） | 本 Serper plan（新） |
|---|---|---|
| Endpoint | `GET customsearch/v1` | `POST google.serper.dev/search` |
| 認證 | `key` + `cx`（兩個） | `X-API-KEY` 單一金鑰 |
| Env vars | `HORUS_GOOGLE_API_KEY` + `..._SEARCH_ENGINE_ID` | `HORUS_SERPER_API_KEY`（單一） |
| 分頁 | 10/頁，`start` offset 到 100 | 單次 `num`（10–100），credit 計費 |
| 結果欄位 | `items[].link/title/snippet` | `organic[].link/title/snippet/date/position` |
| locale | YAGNI 砍掉 gl/hl | **保留 `gl`/`hl`（預設 tw/zh-tw）** ← 見下方理由 |
| 驗證工具 | 無 | **`serp_eval.py` + 手動比對流程** |

**為何違反原 plan 的 YAGNI、保留 gl/hl**：使用者人在台灣，瀏覽器 Google 是 `gl=tw&hl=zh-tw`。Serper 不帶 locale 會回美國英文結果，與使用者實際看到的天差地別，**會讓「驗證結果是否貼近」這個驗收條件根本無法成立**。因此 gl/hl 是本功能的必要參數，不是 nice-to-have。

---

## 前置狀態（已完成，勿重做）

- [x] Step 0：Serper 註冊 + 金鑰實測。`curl -X POST https://google.serper.dev/search -H "X-API-KEY: <key>" -d '{"q":"...","gl":"tw","hl":"zh-tw","num":10}'` 回 200，response shape 為 `{searchParameters, organic, credits}`，`organic[]` 欄位 = `title, link, snippet, date, position`，10 筆 = 1 credit。
- [x] 金鑰放在 repo 根目錄 `.env`（已 gitignore），變數名 `HORUS_SERPER_API_KEY`。

---

## File Structure

| 檔案 | 職責 | 動作 |
|---|---|---|
| `pyproject.toml` | httpx 從 dev group 移到 runtime `dependencies` | Modify |
| `src/horus/config.py` | 新增 `serper_api_key` 欄位 | Modify |
| `src/horus/adapters/serper.py` | Serper adapter（`has_http_mode=True`） | Create |
| `src/horus/adapters/__init__.py` | import + register | Modify |
| `src/horus/cli.py` | http-mode 把 `--limit` 傳進 `fetch_items` | Modify (`:213`) |
| `src/horus/serp_eval.py` | 純函式：`normalize_url`, `overlap_at_k`, `rbo` | Create |
| `tests/adapters/test_serper_adapter.py` | adapter 測試（MockTransport） | Create |
| `tests/test_serp_eval.py` | 純函式單元測試 | Create |
| `scripts/validate_serper.py` | 手動比對 runner（gitignored，本機跑） | Create |
| `CLAUDE.md` | 指令 + 環境變數 + adapter 說明 | Modify |
| `~/.claude/skills/horus-scraping/SKILL.md` | serper adapter 段落 | Modify |
| `~/Documents/Obsidian/shz/Projects/Dev/horus/Backlog/US-005 ...` + `Kanban.md` | 收尾，標記 done | Modify |

**Serper response shape（ground truth，來自實測）**

```jsonc
{
  "searchParameters": {"q": "...", "gl": "tw", "hl": "zh-tw", "num": 10, "type": "search"},
  "organic": [
    {"title": "...", "link": "https://...", "snippet": "...", "date": "2025年3月5日", "position": 1}
  ],
  "credits": 1
}
```

**Credit 計費**：`num<=10` → 1 credit；`num` 11–100 → 2 credits。本 v1 單次請求拿 `num`，不做跨頁分頁（>100 直接 clamp + 警告）。
> ⚠️ **預設成本陷阱**：crawl 的 top-level `--limit` 預設是 **50**（`cli.py:119`），轉發後 `num=50` → **裸跑 `crawl serper --query X` 每查就吃 2 credits**。文件與範例要明示「要 1 credit 請 `--limit 10`」，adapter 在 `num>10` 時也印 stderr 警告。

---

## Chunk 1: Adapter 核心

### Task 1: httpx 移到 runtime deps

**Files:**
- Modify: `pyproject.toml:8-18`（runtime `dependencies`）、`pyproject.toml:23-33`（dev group 移除 httpx）

- [ ] **Step 1: 編輯 pyproject.toml**

把 `"httpx>=0.28.1"` 從 `[dependency-groups] dev` 刪除，加到 `[project] dependencies`：

```toml
dependencies = [
    "playwright>=1.49",
    "click>=8.1",
    "rich>=13.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "markdownify>=1.2.2",
    "fastapi>=0.135.1",
    "uvicorn>=0.41.0",
    "jinja2>=3.1.6",
    "httpx>=0.28.1",
]
```

- [ ] **Step 2: sync**

Run: `uv sync`
Expected: 成功，無錯誤。

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: move httpx to runtime dependencies for serper adapter"
```

---

### Task 2: config 新增 serper_api_key

**Files:**
- Modify: `src/horus/config.py:14-22`
- Test: `tests/test_config_serper.py`（Create）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_config_serper.py
import os
from horus.config import Settings


def test_serper_api_key_read_from_env(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "test-key-123")
    settings = Settings(_env_file=None)  # ignore .env, read only process env
    assert settings.serper_api_key == "test-key-123"


def test_serper_api_key_defaults_none(monkeypatch):
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.serper_api_key is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_config_serper.py -v`
Expected: FAIL（`serper_api_key` 不存在 → AttributeError / 驗證錯誤）

- [ ] **Step 3: 加欄位**

在 `Settings` class（`config.py`，緊接 `db_path` 後）加：

```python
    # Serper.dev Google Search API (env-only; never via CLI flag)
    serper_api_key: str | None = None
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_config_serper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/horus/config.py tests/test_config_serper.py
git commit -m "feat: add serper_api_key setting (env-only)"
```

---

### Task 3: Serper adapter 解析（純函式，先 TDD parse）

**Files:**
- Create: `src/horus/adapters/serper.py`
- Test: `tests/adapters/test_serper_adapter.py`

- [ ] **Step 1: 寫失敗測試（純 JSON 解析）**

```python
# tests/adapters/test_serper_adapter.py
from horus.adapters.serper import SerperAdapter

_FIXTURE = {
    "searchParameters": {"q": "horus crawler", "gl": "tw", "hl": "zh-tw", "num": 10},
    "organic": [
        {"title": "Horus A", "link": "https://a.example/x", "snippet": "sa", "date": "2025年3月5日", "position": 1},
        {"title": "Horus B", "link": "https://b.example/y", "snippet": "sb", "position": 2},
    ],
    "credits": 1,
}


def test_parse_basic_response():
    adapter = SerperAdapter()
    items = adapter.parse_response_json(_FIXTURE, query="horus crawler", gl="tw", hl="zh-tw")
    assert len(items) == 2
    first = items[0]
    assert first.site_id == "serper"
    assert first.url == "https://a.example/x"
    assert first.text == "Horus A"
    assert first.extra["query"] == "horus crawler"
    assert first.extra["rank"] == 1
    assert first.extra["snippet"] == "sa"
    assert first.extra["date"] == "2025年3月5日"
    assert first.extra["gl"] == "tw"
    assert first.extra["hl"] == "zh-tw"


def test_parse_skips_items_without_link_or_title():
    adapter = SerperAdapter()
    body = {"organic": [{"title": "no link", "position": 1}, {"link": "https://x", "position": 2}]}
    items = adapter.parse_response_json(body, query="q", gl="tw", hl="zh-tw")
    assert items == []


def test_parse_zero_results():
    adapter = SerperAdapter()
    items = adapter.parse_response_json({"credits": 1}, query="q", gl="tw", hl="zh-tw")
    assert items == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_serper_adapter.py -v`
Expected: FAIL（`horus.adapters.serper` 不存在）

- [ ] **Step 3: 建立 adapter（含 parse，先不接 HTTP）**

```python
# src/horus/adapters/serper.py
"""Serper.dev Google Search adapter.

Uses Serper's Google Search API (https://google.serper.dev/search) via httpx POST.
No Playwright / browser required. Credentials are env-only (HORUS_SERPER_API_KEY).

Usage:
    horus crawl serper --query "python crawler"
    horus crawl serper --query "python crawler" --limit 30
    horus crawl serper --query "python crawler" --gl us --hl en
"""

import hashlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.models import ScrapedItem

_SERPER_URL = "https://google.serper.dev/search"
_DEFAULT_GL = "tw"
_DEFAULT_HL = "zh-tw"


def _make_item_id(url: str, query: str) -> str:
    """Stable ID: SHA-1 of (url, query) truncated to 16 hex chars."""
    digest = hashlib.sha1(f"{url}|{query}".encode()).hexdigest()  # nosec B324
    return digest[:16]


class SerperAdapter(SiteAdapter):
    """Search Google via Serper.dev and store organic results as ScrapedItems."""

    site_id = "serper"
    display_name = "Serper (Google Search)"
    login_url = ""
    requires_login = False
    has_http_mode = True
    description = "Search Google via Serper.dev API (env key, no browser)"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        # transport injectable for tests (httpx.MockTransport); None = real network.
        self._transport = transport

    # --- SiteAdapter abstract methods (unused in http mode) ---

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        return lambda _url, _body: False

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        return []

    def get_urls(self, **kwargs: Any) -> list[str]:
        return [_SERPER_URL]

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {"name": "--query", "help": "Search query", "required": True, "default": None},
            {"name": "--gl", "help": "Country (geo-location), default tw", "required": False, "default": _DEFAULT_GL},
            {"name": "--hl", "help": "UI language, default zh-tw", "required": False, "default": _DEFAULT_HL},
        ]

    # --- parsing (pure, testable) ---

    def parse_response_json(
        self, body: dict[str, Any], *, query: str, gl: str, hl: str
    ) -> list[ScrapedItem]:
        now = datetime.now(UTC)
        items: list[ScrapedItem] = []
        for idx, org in enumerate(body.get("organic", []), start=1):
            url = (org.get("link") or "").strip()
            title = (org.get("title") or "").strip()
            if not url or not title:
                continue
            items.append(
                ScrapedItem(
                    id=_make_item_id(url, query),
                    site_id="serper",
                    url=url,
                    text=title,
                    timestamp=now,
                    extra={
                        "query": query,
                        "rank": org.get("position", idx),
                        "snippet": org.get("snippet"),
                        "date": org.get("date"),
                        "gl": gl,
                        "hl": hl,
                    },
                )
            )
        return items
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/adapters/test_serper_adapter.py -v`
Expected: PASS（3 個 parse 測試）

- [ ] **Step 5: Commit**

```bash
git add src/horus/adapters/serper.py tests/adapters/test_serper_adapter.py
git commit -m "feat: add serper adapter response parsing"
```

---

### Task 4: 註冊 adapter

**Files:**
- Modify: `src/horus/adapters/__init__.py:1-6, 29-33`

- [ ] **Step 1: 寫失敗測試**

加到 `tests/adapters/test_serper_adapter.py`：

```python
def test_serper_registered():
    from horus.adapters import get_adapter
    assert get_adapter("serper").site_id == "serper"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_serper_adapter.py::test_serper_registered -v`
Expected: FAIL（Unknown site: 'serper'）

- [ ] **Step 3: 註冊**

`__init__.py` import 區加 `from horus.adapters.serper import SerperAdapter`，底部加 `register(SerperAdapter)`。

- [ ] **Step 4: 跑測試 + 確認 list-sites**

Run: `uv run pytest tests/adapters/test_serper_adapter.py::test_serper_registered -v`
Expected: PASS

Run: `uv run horus list-sites`
Expected: 輸出包含 `serper`

- [ ] **Step 5: Commit**

```bash
git add src/horus/adapters/__init__.py tests/adapters/test_serper_adapter.py
git commit -m "feat: register serper adapter"
```

---

### Task 5: fetch_items — query 解析 + HTTP（MockTransport）

**Files:**
- Modify: `src/horus/adapters/serper.py`
- Test: `tests/adapters/test_serper_adapter.py`

- [ ] **Step 1: 寫失敗測試（happy path + limit slice + 缺金鑰）**

```python
import httpx
import pytest


def _ok_handler(n_results=10):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "k"
        import json
        payload = json.loads(request.content)
        organic = [
            {"title": f"t{i}", "link": f"https://e/{i}", "snippet": f"s{i}", "position": i}
            for i in range(1, payload.get("num", 10) + 1)
        ][:n_results]
        return httpx.Response(200, json={"organic": organic, "credits": 1})
    return handler


@pytest.mark.asyncio
async def test_fetch_items_happy(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    adapter = SerperAdapter(transport=httpx.MockTransport(_ok_handler()))
    items = await adapter.fetch_items(query="horus", limit=5)
    assert len(items) == 5
    assert items[0].url == "https://e/1"
    assert items[0].extra["gl"] == "tw"  # default locale applied


@pytest.mark.asyncio
async def test_fetch_items_missing_key_raises(monkeypatch):
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)
    adapter = SerperAdapter(transport=httpx.MockTransport(_ok_handler()))
    with pytest.raises(ValueError, match="HORUS_SERPER_API_KEY"):
        await adapter.fetch_items(query="horus")


@pytest.mark.asyncio
async def test_fetch_items_requires_query(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    adapter = SerperAdapter(transport=httpx.MockTransport(_ok_handler()))
    with pytest.raises(ValueError, match="--query"):
        await adapter.fetch_items()


@pytest.mark.asyncio
async def test_limit_zero_requests_serper_max(monkeypatch):
    # CLI documents --limit 0 = unlimited; for Serper that means the 100 hard cap, not 10
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    seen: dict[str, int] = {}

    def handler(request):
        import json
        seen["num"] = json.loads(request.content)["num"]
        return httpx.Response(200, json={"organic": [], "credits": 1})

    adapter = SerperAdapter(transport=httpx.MockTransport(handler))
    await adapter.fetch_items(query="q", limit=0)
    assert seen["num"] == 100
```

> 注意：`Settings()` 在測試時可能讀到 repo 根的 `.env`（含真實金鑰）。測試以 `monkeypatch.setenv`/`delenv` 控制，並在 adapter 內用 `Settings(_env_file=None)` 讀「僅 process env」，避免測試誤觸真實金鑰、也避免 `delenv` 測試被 `.env` 蓋過。

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_serper_adapter.py -k fetch -v`
Expected: FAIL（`fetch_items` 還沒實作 HTTP）

- [ ] **Step 3: 實作 `_read_queries` / `_search` / `fetch_items`**

加到 `SerperAdapter`：

```python
    def _read_queries(self, **kwargs: Any) -> list[str]:
        query: str | None = kwargs.get("query")
        if query:
            return [query]
        if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
            try:
                lines = [line.strip() for line in sys.stdin if line.strip()]
            except OSError:
                lines = []
            if lines:
                return lines
        raise ValueError("serper adapter requires --query")

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        queries = self._read_queries(**kwargs)
        # Limit semantics: absent → 10 (1 credit); 0 → 100 ("unlimited", Serper hard cap);
        # otherwise clamp to 100. NOTE: via CLI the default is 50 (cli.py:119), so a bare
        # `crawl serper --query X` requests num=50 → costs 2 credits. Pass --limit 10 for 1 credit.
        raw = kwargs.get("limit")
        if raw is None:
            requested = 10
        elif int(raw) == 0:
            requested = 100
        else:
            requested = min(int(raw), 100)
        gl = kwargs.get("gl") or _DEFAULT_GL
        hl = kwargs.get("hl") or _DEFAULT_HL
        num = min(max(requested, 10), 100)  # Serper per-request cap
        if num > 10:
            print(
                f"[serper] num={num} > 10 → this query costs 2 credits (use --limit 10 for 1).",
                file=sys.stderr,
            )

        settings = Settings(_env_file=None) if self._transport else Settings()
        api_key = settings.serper_api_key
        if not api_key:
            raise ValueError(
                "serper adapter requires HORUS_SERPER_API_KEY env var "
                "(set it in .env or export it). Never pass as a CLI flag."
            )

        items: list[ScrapedItem] = []
        async with httpx.AsyncClient(transport=self._transport, timeout=15) as client:
            for query in queries:
                body = await self._search(client, query, num, gl, hl, api_key)
                parsed = self.parse_response_json(body, query=query, gl=gl, hl=hl)
                items.extend(parsed[:requested])
        return items

    async def _search(
        self, client: httpx.AsyncClient, query: str, num: int, gl: str, hl: str, api_key: str
    ) -> dict[str, Any]:
        payload = {"q": query, "gl": gl, "hl": hl, "num": num}
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        resp = await client.post(_SERPER_URL, json=payload, headers=headers)
        # error handling added in Task 6
        resp.raise_for_status()
        return resp.json()
```

> `Settings(_env_file=None)` 只在注入 transport（測試）時用，正式執行讀 `.env`。這讓 `delenv` 測試可靠，正式 CLI 仍能從 `.env` 讀金鑰。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/adapters/test_serper_adapter.py -k fetch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/horus/adapters/serper.py tests/adapters/test_serper_adapter.py
git commit -m "feat: serper fetch_items with httpx (env key, locale defaults)"
```

---

### Task 6: 錯誤處理（401 不重試、429 Retry-After、5xx 重試一次）

**Files:**
- Modify: `src/horus/adapters/serper.py`（`_search`）
- Test: `tests/adapters/test_serper_adapter.py`

- [ ] **Step 1: 寫失敗測試**

```python
@pytest.mark.asyncio
async def test_401_raises_no_retry(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"message": "Unauthorized"})

    adapter = SerperAdapter(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="Invalid"):
        await adapter.fetch_items(query="q")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_5xx_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"organic": [{"title": "t", "link": "https://e/1", "position": 1}], "credits": 1})

    adapter = SerperAdapter(transport=httpx.MockTransport(handler))
    items = await adapter.fetch_items(query="q", limit=1)
    assert len(items) == 1
    assert calls["n"] == 2
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_serper_adapter.py -k "401 or 5xx" -v`
Expected: FAIL

- [ ] **Step 3: 收斂錯誤處理**

把 `_search` 的 HTTP 呼叫換成：

```python
    async def _search(
        self, client: httpx.AsyncClient, query: str, num: int, gl: str, hl: str, api_key: str
    ) -> dict[str, Any]:
        payload = {"q": query, "gl": gl, "hl": hl, "num": num}
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

        resp = await client.post(_SERPER_URL, json=payload, headers=headers)

        if resp.status_code in (401, 403):
            raise ValueError(f"Invalid Serper API key or access denied (HTTP {resp.status_code})")
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                await asyncio.sleep(min(float(retry_after), 10))
                resp = await client.post(_SERPER_URL, json=payload, headers=headers)
            if resp.status_code == 429:
                raise RuntimeError("Serper rate limited (429)")
        if resp.status_code >= 500:
            await asyncio.sleep(1)
            resp = await client.post(_SERPER_URL, json=payload, headers=headers)

        resp.raise_for_status()
        return resp.json()
```

加 `import asyncio` 到檔案頂端。

> 測試的 5xx retry 不真的睡 1 秒會拖慢嗎？`asyncio.sleep(1)` 在單一測試可接受；若要更快可 `monkeypatch` `asyncio.sleep`。本 plan 保持簡單，不 mock sleep。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/adapters/test_serper_adapter.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/horus/adapters/serper.py tests/adapters/test_serper_adapter.py
git commit -m "feat: serper error handling (401 no-retry, 429 retry-after, 5xx retry once)"
```

---

### Task 7: cli.py 把 `--limit` 傳進 http-mode

**Files:**
- Modify: `src/horus/cli.py:210-213`

**背景**：`--limit` 是 crawl 的 top-level Click option（`cli.py:119`），會被解析進 `limit` 變數，**不會**進 `kwargs`。所以 `fetch_items(**kwargs)` 收不到 limit。ddg 因為忽略 limit 沒事，但 serper 需要它。

- [ ] **Step 1: 寫失敗測試（CLI 層）**

```python
# tests/adapters/test_serper_cli.py
from click.testing import CliRunner
from unittest.mock import AsyncMock, patch
from horus.cli import main


def test_crawl_passes_limit_to_fetch_items(monkeypatch, tmp_path):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    with patch("horus.adapters.serper.SerperAdapter.fetch_items", new=AsyncMock(return_value=[])) as m:
        runner = CliRunner()
        result = runner.invoke(main, ["crawl", "serper", "--query", "x", "--limit", "30"])
        assert result.exit_code == 0, result.output
        # fetch_items must have been called with limit=30
        assert m.call_args.kwargs.get("limit") == 30
        assert m.call_args.kwargs.get("query") == "x"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_serper_cli.py -v`
Expected: FAIL（limit 不在 call kwargs）

- [ ] **Step 3: 改 cli.py http-mode 分支**

`cli.py:213` 由：

```python
            items = await adapter.fetch_items(**kwargs)
```

改為（讓 `--limit` 進入 http-mode；kwargs 若已含 limit 則以 kwargs 為準，避免 serve 路徑衝突）：

```python
            http_kwargs = {"limit": limit, **kwargs}
            items = await adapter.fetch_items(**http_kwargs)
```

- [ ] **Step 4: 跑測試 + 確認 ddg 沒被弄壞**

Run: `uv run pytest tests/adapters/test_serper_cli.py tests/adapters/test_ddg_adapter.py -v`
Expected: PASS（ddg `fetch_items(**kwargs)` 忽略多出來的 limit，不受影響）

> 若 `tests/adapters/test_ddg_adapter.py` 不存在，改跑 `uv run pytest tests/ -k ddg -v` 確認 ddg 相關測試全綠。

- [ ] **Step 5: 同步修 serve 路徑(避免 web UI 的 limit 對 http-mode 失效)**

`src/horus/serve/crawler_manager.py:136` 同樣是 `items = await adapter.fetch_items(**job.kwargs)`，HTTP-mode 也沒轉發 limit。serve 的 crawl 表單有獨立 limit 欄位（`templates/crawl.html`），`_run()` 收得到（約 `crawler_manager.py:111`）但 http 分支忽略它。

先讀檔確認 limit 在 `_run()` 裡的實際變數名/來源（可能在 `job` 或 `_run` 參數），再套同一 pattern：

```python
http_kwargs = {"limit": <serve_limit>, **job.kwargs}
items = await adapter.fetch_items(**http_kwargs)
```

加一個 serve 層測試（route 或 `CrawlerManager`）驗證 http-mode 會把 limit 帶進 `fetch_items`。若 serve 的 limit 來源與 CLI 不同名，以實際程式碼為準。

- [ ] **Step 6: Commit**

```bash
git add src/horus/cli.py src/horus/serve/crawler_manager.py tests/adapters/test_serper_cli.py tests/
git commit -m "feat: pass --limit into http-mode fetch_items (cli + serve)"
```

---

## Chunk 2: 結果品質驗證工具

目標：能量化「Serper 回的結果」vs「瀏覽器 Google（無痕 + gl=tw&hl=zh-TW&pws=0）」有多接近。指標：**overlap@k**（集合重疊）+ **RBO**（排序敏感）。純函式可單元測試、無 network。

### Task 8: serp_eval 純函式

**Files:**
- Create: `src/horus/serp_eval.py`
- Test: `tests/test_serp_eval.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_serp_eval.py
from horus.serp_eval import normalize_url, overlap_at_k, rbo


def test_normalize_collapses_host_scheme_slash_and_drops_tracking():
    # www / scheme / trailing-slash differences collapse; tracking params dropped
    assert normalize_url("https://www.Example.com/Path/?utm_source=x") == normalize_url(
        "http://example.com/Path"
    )


def test_normalize_keeps_meaningful_query_params():
    # non-tracking params are identity-bearing (e.g. youtube watch?v=) and kept
    assert normalize_url("https://example.com/p?id=1&utm_source=x") == "example.com/p?id=1"


def test_normalize_unwraps_google_redirect():
    # manual browser SERP copies often yield google redirect wrappers
    assert (
        normalize_url("https://www.google.com/url?q=https%3A%2F%2Ftarget.com%2Fa&sa=U")
        == "target.com/a"
    )


def test_overlap_at_k_counts_shared_normalized_urls():
    a = ["https://x.com/1", "https://y.com/2", "https://z.com/3"]
    b = ["http://www.x.com/1/", "https://q.com/9", "https://z.com/3"]
    assert overlap_at_k(a, b, k=3) == 2 / 3  # x and z shared, normalized


def test_rbo_identical_is_high_and_disjoint_is_zero():
    a = [f"https://e/{i}" for i in range(10)]
    assert rbo(a, a) > 0.6           # finite-truncation: identical 10-lists ~0.65 at p=0.9
    b = [f"https://other/{i}" for i in range(10)]
    assert rbo(a, b) == 0.0


def test_rbo_top_swaps_hurt_more_than_tail_swaps():
    a = [f"https://e/{i}" for i in range(10)]
    swap_top = a.copy(); swap_top[0], swap_top[1] = swap_top[1], swap_top[0]
    swap_tail = a.copy(); swap_tail[8], swap_tail[9] = swap_tail[9], swap_tail[8]
    assert rbo(a, swap_tail) > rbo(a, swap_top)  # tail swaps matter less (top-weighted)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_serp_eval.py -v`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 實作 serp_eval.py**

```python
# src/horus/serp_eval.py
"""Pure metrics for comparing two ranked SERP URL lists.

Used to validate that `serper` results approximate the browser's Google results.
overlap_at_k = set overlap (coverage); rbo = top-weighted rank similarity.
"""

from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "gclid", "fbclid", "ref", "ref_src")


def normalize_url(url: str) -> str:
    """Canonicalize for comparison: lowercase host, drop www/scheme-diff/tracking/trailing slash.

    Also unwraps Google redirect links (google.com/url?q=<target>), which manual
    browser SERP collection frequently produces — without this, every redirect
    URL would falsely mismatch the real target.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Unwrap Google redirect wrappers before anything else
    if host.endswith("google.com") and parts.path == "/url":
        qs = parse_qs(parts.query)
        target = (qs.get("q") or qs.get("url") or [None])[0]
        if target:
            return normalize_url(unquote(target))
    path = parts.path.rstrip("/")
    # drop tracking query params, keep meaningful ones, sorted for stability
    kept = []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0]
        if any(key.startswith(p) for p in _TRACKING_PREFIXES):
            continue
        kept.append(pair)
    query = "&".join(sorted(kept))
    return urlunsplit(("", host, path, query, ""))


def overlap_at_k(list1: list[str], list2: list[str], k: int = 10) -> float:
    """Fraction of top-k items of list1 whose normalized URL also appears in top-k of list2."""
    if k <= 0:
        return 0.0
    s1 = [normalize_url(u) for u in list1[:k]]
    s2 = {normalize_url(u) for u in list2[:k]}
    if not s1:
        return 0.0
    return sum(1 for u in s1 if u in s2) / len(s1)


def rbo(list1: list[str], list2: list[str], p: float = 0.9) -> float:
    """Rank-Biased Overlap in [0, 1]. Top-weighted; p=0.9 ≈ top-10 emphasis.

    Note: for identical *finite* lists of length n this is < 1 (truncation), a
    known RBO property. Use it for *relative* comparison, not an absolute pass mark.
    """
    n1 = [normalize_url(u) for u in list1]
    n2 = [normalize_url(u) for u in list2]
    depth = max(len(n1), len(n2))
    if depth == 0:
        return 1.0
    seen1: set[str] = set()
    seen2: set[str] = set()
    score = 0.0
    for d in range(depth):
        if d < len(n1):
            seen1.add(n1[d])
        if d < len(n2):
            seen2.add(n2[d])
        agreement = len(seen1 & seen2) / (d + 1)
        score += (p**d) * agreement
    return (1 - p) * score
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_serp_eval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/horus/serp_eval.py tests/test_serp_eval.py
git commit -m "feat: add serp_eval metrics (normalize_url, overlap_at_k, rbo)"
```

---

### Task 9: 手動比對 runner（本機工具）

**Files:**
- Create: `scripts/validate_serper.py`（注意：`scripts/` 已 gitignore，本機用，不進版控）

- [ ] **Step 1: 建立 runner**

```python
# scripts/validate_serper.py
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx"]
# ///
"""Compare serper results vs browser Google (manual paste).

Workflow:
  1. 在無痕視窗開:
     https://www.google.com/search?q=<QUERY>&gl=tw&hl=zh-TW&pws=0
     依序複製 top-10 結果網址，一行一個，存成 browser.txt
  2. uv run scripts/validate_serper.py "<QUERY>" browser.txt
"""
import asyncio
import sys

import httpx

from horus.config import Settings
from horus.serp_eval import overlap_at_k, rbo


async def serper_urls(query: str, k: int = 10) -> list[str]:
    key = Settings().serper_api_key
    if not key:
        sys.exit("Set HORUS_SERPER_API_KEY in .env")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "gl": "tw", "hl": "zh-tw", "num": k},
            headers={"X-API-KEY": key},
        )
        r.raise_for_status()
        return [o["link"] for o in r.json().get("organic", [])][:k]


def main() -> None:
    query, browser_file = sys.argv[1], sys.argv[2]
    browser = [ln.strip() for ln in open(browser_file) if ln.strip()]
    serper = asyncio.run(serper_urls(query, k=len(browser) or 10))
    print(f"overlap@{len(browser)}: {overlap_at_k(serper, browser, k=len(browser)):.2f}")
    print(f"RBO:               {rbo(serper, browser):.3f}")
    print("\nserper:")
    for i, u in enumerate(serper, 1):
        print(f"  {i:>2} {u}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手動驗證（記錄一次基準）**

Run（範例）:
```bash
# 先在無痕 Google 抓 top-10 存成 browser.txt，再：
uv run scripts/validate_serper.py "fastapi tutorial" browser.txt
```
Expected: 印出 `overlap@10` 與 `RBO`，以及 serper 的 top-10。

**驗收判讀（門檻可調）**：以 overlap@10 為主指標、RBO 為排序輔助。建議基準：**top-3 URL 至少命中 2 個、overlap@10 ≥ 0.5、RBO ≥ 0.5** 視為「夠接近」。若 overlap 異常低，先檢查 locale（gl/hl 是否對齊瀏覽器）與時間點。

> runner 在 gitignored `scripts/`，屬本機驗證工具；可信賴的核心邏輯（overlap/RBO/normalize）已在 Task 8 進版控且有單元測試。

---

## Chunk 3: 文件與收尾

### Task 10: 更新 CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`（常用指令、環境變數、adapter 說明、專案結構）

- [ ] **Step 1: 編輯**
  - 「常用指令」加：
    ```bash
    horus crawl serper --query "fastapi tutorial" --limit 10   # 1 credit/查（Serper API，需金鑰）
    horus crawl serper --query "..." --limit 30 --gl tw --hl zh-tw   # >10 筆 = 2 credits/查
    ```
  - 「環境變數」加：`HORUS_SERPER_API_KEY=...   # Serper.dev Google Search API key（env-only）`
  - 「專案結構」adapters 區加 `serper.py`，src 區加 `serp_eval.py`
  - 新增「Serper Adapter 說明」段落：env-only 金鑰、gl/hl 預設 tw/zh-tw、**credit 計費（≤10 筆=1 credit、11–100 筆=2 credits；CLI 預設 `--limit 50` 會吃 2 credits，要省請帶 `--limit 10`）**、單次請求不跨頁（>100 clamp）、extra 欄位 `query/rank/snippet/date/gl/hl`、結果品質用 `serp_eval` 驗證

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document serper adapter in CLAUDE.md"
```

---

### Task 11: 更新 horus-scraping skill

**Files:**
- Modify: `~/.claude/skills/horus-scraping/SKILL.md`

- [ ] **Step 1: 加 serper adapter 段落**（與 ddg 並列：用途、`--query`/`--gl`/`--hl`/`--limit`、需設 `HORUS_SERPER_API_KEY`、credit 計費、與 ddg 的取捨）。CLAUDE.md「開發規範」要求 adapter 變更必須同步此 skill。

- [ ] **Step 2: 確認 SKILL.md 與實作一致**（site_id、選項、env var 名稱）。skill 在 `~/.claude/`，不屬 repo 版控，不需 commit。

---

### Task 12: 收尾 backlog / Kanban + 全量驗證

**Files:**
- Modify: `~/Documents/Obsidian/shz/Projects/Dev/horus/Backlog/US-005 Google Search Adapter.md`、`Kanban.md`、`Progress.md`

- [ ] **Step 1: 全量驗證**

```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ty check src/    # 或 uv run mypy src/
uv run horus list-sites   # 應含 serper
```
Expected: 全綠、`serper` 出現。

- [ ] **Step 2: 更新 US-005 卡 / Kanban**
  - US-005 卡：標題與內文改為 Serper（或新增 US-006 並把 US-005 標記為「superseded by Serper」），勾選驗收條件。
  - `Kanban.md`：US-005 從 Backlog 移到 Done。
  - `Progress.md`：依時間倒序加一筆「Serper adapter + serp_eval 驗證工具完成」。

- [ ] **Step 3: 最終 commit**

```bash
git add -A
git commit -m "chore: mark serper search adapter done, update project backlog"
```

> 用 coding-finisher agent 收尾（test + lint + typecheck + commit/push），遵循 CLAUDE.md 規範；commit message 結尾加 `Co-Authored-By: xero7689 <shzlee217@gmail.com>`。

---

## 完成標準

- [ ] httpx 在 runtime deps；`uv sync` 綠
- [ ] `horus list-sites` 顯示 `serper`
- [ ] `horus crawl serper --query "..."` 能取得 Google 結果並存 DB
- [ ] `--limit` / `--gl` / `--hl` 生效；缺金鑰錯誤訊息清楚指向 `HORUS_SERPER_API_KEY`
- [ ] 金鑰 env-only，未出現在 `crawl_log` / serve API（不接 CLI flag）
- [ ] 所有 adapter 測試用 `httpx.MockTransport`，CI 無真實 network call
- [ ] `serp_eval`（overlap@k + RBO + normalize_url）有單元測試
- [ ] 手動比對 runner 可跑，記錄過一次 overlap/RBO 基準
- [ ] `uv run pytest` / `ruff check` / `ty check` 全綠
- [ ] CLAUDE.md + horus-scraping skill 同步更新
