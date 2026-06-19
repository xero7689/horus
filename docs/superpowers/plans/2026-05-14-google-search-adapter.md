# Plan: Google Search Adapter (Programmable Search / Custom Search JSON API)

Status: ready (post external review)
Author: Peter
Date: 2026-05-14
Revised: 2026-05-14 (incorporated codex external review)

## 0. Step 0 — Go / No-Go: 先確認 API access

**這是硬性 gate，沒過不動工。**

Google docs 明寫 Custom Search JSON API 已 closed to new customers，2027-01-01 全面 sunset。新使用者可能根本拿不到 key。動工前 Peter 必須：

1. 在 https://programmablesearchengine.google.com/controlpanel/all 建立一個 Programmable Search Engine
2. 開啟 "Search the entire web"，取得 `cx`（Search Engine ID）
3. 在 Google Cloud Console 開新 project，啟用 Custom Search API，產生 API key
4. 用 curl 確認可以拿到結果：
   ```bash
   curl "https://www.googleapis.com/customsearch/v1?key=$KEY&cx=$CX&q=test"
   ```

**curl 失敗的情境**：
- 收到 `Custom Search API has not been used in project` → 需要在 console 啟用 API
- 收到 `API key not valid` 或 `403 not authorized` → API 可能真的對新使用者關了

→ 如果 Step 0 過不去，停掉本 plan，改評估替代方案（Vertex AI Search、SerpAPI、Serper.dev、Bing Web Search API）並另開 plan。

## 1. 目標

新增 `google` adapter，讓使用者可以用

```
horus crawl google --query "python web crawler"
horus crawl google --query "python web crawler" --limit 30
```

把 Google 搜尋結果存進 SQLite 的 `items` 表，跟現有 `ddg` adapter 的使用體驗一致。

## 2. 選擇的方案 — Custom Search JSON API (Programmable Search)

### 2.1 API 概覽

- **Endpoint**：`GET https://www.googleapis.com/customsearch/v1`
- **Required params**：`key`（API key）、`cx`（Programmable Search Engine ID）、`q`（query）
- **Useful optional params**：`num`（每頁 1–10）、`start`（offset，1-based，最大 91）、`hl`、`gl`、`lr`
- **Auth**：API key as query string
- **Hard cap**：100 results / query 總計（10 pages × 10 results）

### 2.2 Free / paid tier

- Free：100 queries/day
- Paid：$5 / 1000 queries，上限 10k queries/day

### 2.3 Response shape

```jsonc
{
  "kind": "customsearch#search",
  "queries": {
    "request": [{"searchTerms": "...", "count": 10, "startIndex": 1}],
    "nextPage": [{"startIndex": 11}]    // 只有非最後一頁才有
  },
  "searchInformation": {"totalResults": "...", "searchTime": 0.42},
  "items": [
    {
      "kind": "customsearch#result",
      "title": "...",
      "htmlTitle": "...",
      "link": "https://...",
      "displayLink": "example.com",
      "snippet": "...",
      "htmlSnippet": "...",
      "pagemap": { /* schema.org metadata — 不存 */ }
    }
  ]
}
```

## 3. 實作計畫

### 3.1 Runtime deps 先補

把 `httpx` 從 `[dependency-groups] dev` **移到 `[project] dependencies`**：

```toml
dependencies = [
    ...
    "httpx>=0.28.1",   # ← 移上來，runtime 用
]
```

### 3.2 Credentials 管理 — 強制 env-only

**不開放 CLI flag**。理由：`cli.py:218` 與 `serve/crawler_manager.py:142` 都用 `str(kwargs)` 寫 `crawl_log` 表；`crawler_manager.py:65` 把 raw `kwargs` 序列化到 web API。若允許 `--api-key`，會落到 SQLite + 公開到 serve endpoint。

`src/horus/config.py` 新增：

```python
google_api_key: str | None = None         # HORUS_GOOGLE_API_KEY
google_search_engine_id: str | None = None  # HORUS_GOOGLE_SEARCH_ENGINE_ID
```

讀取順序：
1. 環境變數 `HORUS_GOOGLE_API_KEY` / `HORUS_GOOGLE_SEARCH_ENGINE_ID`
2. CWD 的 `.env`（pydantic-settings 預設行為，**不是** `~/.horus/.env`）
3. 兩者都缺 → adapter `fetch_items()` 抛 `ValueError("Set HORUS_GOOGLE_API_KEY and HORUS_GOOGLE_SEARCH_ENGINE_ID env vars. ...")`

Adapter 內部直接 `Settings()` 讀取（不接 CLI kwargs）。

### 3.3 新檔案 `src/horus/adapters/google.py`

跟 `ddg.py` 同形：`has_http_mode = True`，實作 `fetch_items()`。

```python
class GoogleSearchAdapter(SiteAdapter):
    site_id = "google"
    display_name = "Google Search (Programmable Search)"
    login_url = ""
    requires_login = False
    has_http_mode = True
    description = "Search Google via Custom Search JSON API"
```

要實作的方法：

| Method | 行為 |
|---|---|
| `get_response_filter()` | `lambda _u, _b: False`（http mode 不用） |
| `parse_response(body)` | `[]`（http mode 不用） |
| `get_urls(**kw)` | `[ENDPOINT]`（unused 但 abstract 要實作） |
| `get_crawl_options()` | 只宣告 `--query`（required）跟 `--limit`（adapter 自己處理，預設 10、cap 100） |
| `_read_queries(**kw)` | 支援 `--query` 或 stdin 多行（同 ddg） |
| `fetch_items(**kw)` | 主邏輯：讀 query → 分頁打 API → 解析 |
| `parse_response_json(body, query, rank_offset)` | 把 `items[]` 轉成 `ScrapedItem` |

**注意**：`--limit` 在 `crawl` 命令的 Click 層是 top-level option，**不會** 透過 `_parse_extra_args` 傳給 `fetch_items`。所以 adapter 必須宣告自己的 `--limit` 並從 `extra_args` 接，與 Click 的 `--limit` 共存（位置在 `crawl google` 之後）。

`fetch_items()` 概略：

```python
async def fetch_items(self, **kwargs) -> list[ScrapedItem]:
    queries = self._read_queries(**kwargs)
    requested = int(kwargs.get("limit") or 10)
    if requested > 100:
        console.warn("Google API caps at 100 results/query; clamping")
    limit = min(requested, 100)

    settings = Settings()
    api_key = settings.google_api_key
    cx = settings.google_search_engine_id
    if not api_key or not cx:
        raise ValueError(
            "Google adapter requires HORUS_GOOGLE_API_KEY and "
            "HORUS_GOOGLE_SEARCH_ENGINE_ID. See plan §3.2."
        )

    items: list[ScrapedItem] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for query in queries:
            items.extend(await self._search(client, query, limit, api_key, cx))
    return items
```

`_search()` 分頁邏輯：每次 `num=min(10, remaining)`，從 `start=1` 開始，到拿足 `limit` 或 response 沒有 `nextPage` 為止。

### 3.4 錯誤處理（收斂版）

只對「明確指出可重試」的錯誤重試，其他直接抛：

| Status | 處理 |
|---|---|
| 200 | 正常 |
| 401 | `ValueError("Invalid API key")` — 不重試 |
| 403 | 印出 Google 回傳的 `error.message`（quota / invalid cx / API not enabled）— 不重試 |
| 429 | 若有 `Retry-After` header → sleep 並重試一次；否則抛 `RuntimeError("Rate limited")` |
| 5xx | 重試一次（1s 後）；再失敗就抛 |
| 200 但沒 `items` 欄位 | 視為 0 結果，回傳 `[]` |

不對 403 做猜測性 retry（Google 通用 API 雖然 rateLimit 偶有 403，但 Custom Search 文件只列 429，保守處理）。

### 3.5 ScrapedItem 對應

```python
ScrapedItem(
    id=_make_item_id(item["link"], query),  # SHA-1 of (url|query)
    site_id="google",
    url=item["link"],
    text=item["title"],
    timestamp=datetime.now(UTC),
    extra={
        "query": query,
        "rank": absolute_rank,           # 1..N across all pages
        "snippet": item.get("snippet"),
        "display_link": item.get("displayLink"),
    },
)
```

**不存 `pagemap`**：巢狀 schema.org metadata 讓 row 過肥，且很少用到。

### 3.6 註冊 adapter

`src/horus/adapters/__init__.py`：

```python
from horus.adapters.google import GoogleSearchAdapter
register(GoogleSearchAdapter)
```

### 3.7 測試 — 改用 `httpx.MockTransport`

不引入新 dep（`pytest-recording` 目前未安裝）。用 httpx 內建 `MockTransport`：

```python
async def test_pagination():
    def handler(request):
        start = int(request.url.params.get("start", "1"))
        return httpx.Response(200, json=_fixture_page(start))
    transport = httpx.MockTransport(handler)
    # inject into adapter via constructor or monkeypatch
```

| Test | 目的 |
|---|---|
| `test_parse_basic_response` | 純 JSON parsing |
| `test_pagination_fetches_until_limit` | 30 筆橫跨 3 頁 |
| `test_pagination_stops_when_no_next_page` | 中途沒 `nextPage` 就停 |
| `test_missing_credentials_raises` | 缺 env var → ValueError |
| `test_zero_results_returns_empty` | response 沒 `items` → `[]` |
| `test_429_with_retry_after_succeeds_on_retry` | 第一次 429 + Retry-After，第二次 200 |
| `test_401_raises_without_retry` | 401 → ValueError，呼叫次數 = 1 |

Adapter 必須允許注入 `httpx.AsyncClient`（或 transport）以便測試。

### 3.8 CLI 使用流程

```bash
# 1. 申請 cx 與 API key（見 §0）
# 2. 設定環境變數（CWD 的 .env 或 export）
export HORUS_GOOGLE_API_KEY="..."
export HORUS_GOOGLE_SEARCH_ENGINE_ID="..."

# 3. 搜尋
horus crawl google --query "fastapi tutorial"
horus crawl google --query "fastapi tutorial" --limit 30
echo -e "fastapi\ndjango" | horus crawl google  # stdin 多 query（同 ddg）
```

### 3.9 文件更新

- `CLAUDE.md`：「常用指令」加範例；環境變數區補上 `HORUS_GOOGLE_API_KEY` / `HORUS_GOOGLE_SEARCH_ENGINE_ID`
- `~/.claude/skills/horus-scraping/SKILL.md`：新增 google adapter 段落

## 4. v1 Scope（YAGNI）

**只做**：`--query`、stdin、`--limit`、env credentials、分頁到 100、收斂錯誤處理、`extra` 存 `query/rank/snippet/display_link`。

**砍掉到後續**：
- `--site example.com`（siteSearch param）— 等真有 use case 再加
- `--date-restrict`（dateRestrict param）— 同上
- `--hl` / `--gl` / `--lr`（locale params）— 同上
- 自動付費啟用 / quota 監控
- VCR cassette（要用 `pytest-recording` 再開另一個 plan 統一處理整個專案的 HTTP 測試慣例）

## 5. 風險

| 風險 | 嚴重度 | mitigation |
|---|---|---|
| API 對新使用者關閉 | **高** | §0 hard gate，先實測 |
| 2027-01-01 sunset | 中 | 文件加註，使用者自決定 |
| Free tier 只有 100 q/day | 中 | 配額用罄錯誤訊息清楚 |
| Hard cap 100 results/query | 低 | `--limit` cap 100，超過警告 |
| API key 洩漏 | 中 | env-only，不接 CLI flag（§3.2） |

## 6. 完成標準

- [ ] §0 manual curl 成功
- [ ] httpx 已移到 runtime deps
- [ ] `horus list-sites` 顯示 `google`
- [ ] 有 env credential 時 `horus crawl google --query "..."` 能取得結果並存 DB
- [ ] 缺 credential 時錯誤訊息清楚指向 env vars
- [ ] 所有測試用 `httpx.MockTransport`，CI 無 network call
- [ ] `uv run pytest`、`uv run ruff check`、`uv run ty check src/` 全綠
- [ ] CLAUDE.md + horus-scraping skill 同步更新
