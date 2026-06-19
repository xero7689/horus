# Horus

通用 browser crawler 框架，支援插拔式 site adapter，特別擅長處理需要登入的網站。

## 技術棧

- Python 3.13+, uv
- Playwright (headless Chromium) — browser 核心
- Pydantic v2 + pydantic-settings — 模型與設定
- SQLite + FTS5 (trigram) — 持久儲存與全文搜尋
- Click + Rich — CLI

## 專案結構

```
src/horus/
├── cli.py              # Click CLI（login, crawl, list-sites, show, search, pages, export, stats）
├── config.py           # Settings（HORUS_* env vars，~/.horus/ 路徑管理）
├── models.py           # ScrapedItem, ScrapedPage, CrawlResult, SiteAdapterConfig
├── serp_eval.py        # 純函式 SERP 比對指標（normalize_url, overlap_at_k, rbo）
├── core/
│   ├── browser.py      # BaseBrowser（Playwright 生命週期，save_login_state）
│   ├── scraper.py      # BaseScraper（scrape：scroll + response intercept；scrape_page：HTML → Markdown）
│   └── storage.py      # HorusStorage（SQLite + FTS5；items + pages 兩張表）
└── adapters/
    ├── base.py         # SiteAdapter ABC（has_page_mode flag + 3 abstract methods）
    ├── __init__.py     # Registry（register, get_adapter, list_adapters）
    ├── threads.py      # Threads adapter（GraphQL 攔截）
    ├── twitter.py      # Twitter/X adapter（GraphQL 攔截，anonymous 可用）
    ├── web.py          # GenericWebAdapter（任意公開網頁 → Markdown，has_page_mode=True）
    └── serper.py       # Serper adapter（Google Search via httpx，has_http_mode=True）
tests/
├── conftest.py         # storage fixture（in-memory SQLite）
├── test_storage.py
├── fixtures/twitter/   # 真實 X GraphQL response（parser 測試用）
└── adapters/
    ├── test_threads_adapter.py
    ├── test_twitter_adapter.py
    └── test_web_adapter.py
```

## 常用指令

```bash
horus login threads                              # 開 browser 手動登入，儲存 ~/.horus/states/threads.json
horus crawl threads --user @username             # 爬取貼文（增量）
horus crawl threads --user @username --mode replies  # 爬取回覆
horus crawl threads --url https://...            # 爬特定 URL
horus crawl twitter --user @NASA                 # 爬 X 貼文（含置頂，anonymous 可用）
horus crawl twitter --user @NASA --mode replies  # 爬 X 回覆
horus crawl twitter --url https://x.com/NASA/status/123  # 爬單一貼文/串
horus crawl web --url https://example.com        # 爬公開網頁，存 pages 表
horus crawl web --url https://example.com --output ./pages/   # 同時輸出 .md 檔
horus crawl web --url-list urls.txt --output ./pages/          # 批次爬取
horus crawl serper --query "fastapi tutorial" --limit 10   # 1 credit/查（Serper API，需金鑰）
horus crawl serper --query "..." --limit 30 --gl tw --hl zh-tw   # >10 筆 = 2 credits/查
horus list-sites                                 # 列出可用 adapters
horus show --site threads --limit 20             # 顯示已儲存 items
horus pages --site web --limit 10                # 顯示已儲存 pages
horus search "關鍵字" --site threads             # FTS 搜尋（支援中文）
horus export --site threads --format json -o out.json
horus export --site threads --format csv -o out.csv
horus export --site web --format markdown --output ./export/  # 從 DB 匯出 .md 檔
horus stats                                      # 統計資訊
```

## 開發規範

- 新增或修改 adapter 後，**必須更新** `~/.claude/skills/horus-scraping/SKILL.md`，確保 skill 與實作同步。
- 使用 `horus-scraping` skill 作為開發新 adapter 的參考指南。

## 新增 Site Adapter（3 步驟）

1. 建立 `src/horus/adapters/mysite.py`，繼承 `SiteAdapter`，實作 3 個 abstract method：
   - `get_response_filter()` → `(url, body) -> bool`：過濾攔截的 HTTP response
   - `parse_response(body)` → `list[ScrapedItem]`：解析成通用格式
   - `get_urls(**kwargs)` → `list[str]`：根據 CLI 參數產生 URL 列表

2. 在 `src/horus/adapters/__init__.py` 加：
   ```python
   from horus.adapters.mysite import MySiteAdapter
   register(MySiteAdapter)
   ```

3. 執行 `horus list-sites` 確認出現

## 關鍵設計

- **ScrapedItem.extra**：site-specific 欄位存 JSON dict，避免多表 migration
- **response_filter 簽名**：`(url: str, body: dict) -> bool`，雙重過濾（URL pattern + body 結構）
- **State 儲存**：`~/.horus/states/<site_id>.json`，per-site Playwright storage_state
- **FTS5 trigram**：支援中文搜尋，短查詢（<3字）自動 fallback LIKE
- **WAL mode**：SQLite write-ahead logging
- **增量爬取**：`crawl` 指令預設從上次最新 timestamp 開始（`get_latest_timestamp`）
- **has_page_mode**：`SiteAdapter.has_page_mode = True` 時 CLI 走 `scrape_page()` 路徑（HTML → Markdown），存入 `pages` 表；`False` 走 response 攔截路徑存入 `items` 表
- **markdownify**：HTML → Markdown 轉換，保留結構、去除 script/style/nav/footer

## 環境變數

```
HORUS_BASE_DIR=~/.horus        # 資料根目錄
HORUS_DB_PATH=~/.horus/data.db # SQLite 路徑（覆蓋預設）
HORUS_HEADLESS=true            # headless mode
HORUS_SCROLL_DELAY_MIN=3.0
HORUS_SCROLL_DELAY_MAX=8.0
HORUS_REQUEST_JITTER=2.0
HORUS_MAX_PAGES=50
HORUS_SERPER_API_KEY=...        # Serper.dev Google Search API key（env-only）
```

## 開發指令

```bash
uv run pytest tests/ -v                # 執行所有測試
uv run pytest tests/test_storage.py   # 單一測試檔案
uv run ruff check src/ tests/          # Lint
uv run mypy src/                       # Type check
uv run horus --help                    # 確認 CLI 可用
```

## Threads Adapter 說明

- 攔截 GraphQL responses（URL 含 `graphql` 且 body 含 `mediaData`）
- 自動偵測 posts/replies mode：thread_items 有 1 個 = posts，2+ 個 = replies
- extra 欄位：`like_count`, `reply_count`, `repost_count`, `media_type`, `media_urls`,
  `is_reply`, `parent_post_id`, `conversation_id`, `reply_to_username`

## Twitter (X) Adapter 說明

- 走 response 攔截（同 Threads，`has_page_mode=False`），攔截 GraphQL `UserTweets` /
  `UserTweetsAndReplies` / `TweetDetail`，依導航頁面自動觸發
- **anonymous 可用**：未登入即可爬公開 profile；量大時 `horus login twitter` 存 state
  較穩（X 對 guest token 限流兇）。`requires_login=False`
- `--user @name`（posts）/ `--user @name --mode replies` / `--url <status>`；stdin 多 user
- **不支援 `--with-comments`**（X 無 SSR thread_items 等價物，回覆走 `--mode replies` 或 `--url`）
- 關鍵解析：author 走新路徑 `core.user_results.result.core`（legacy 已空）；轉推 unwrap
  `retweeted_status_result` 取內層完整內容（外層 `full_text` 是 `RT @…` 截斷版），id/timestamp
  保留外層；長文取 `note_tweet`；置頂貼文在 `TimelinePinEntry` instruction
- extra 欄位：`like_count`, `retweet_count`, `reply_count`, `quote_count`, `bookmark_count`,
  `view_count`, `media_type`, `media_urls`, `lang`, `is_reply`, `is_retweet`, `retweeted_by`,
  `is_quote`, `parent_post_id`, `conversation_id`, `reply_to_username`
- 自串/回覆沿用 `conversation_id`/`parent_post_id`/`is_reply` 慣例，接 `thread_tree` 與前端詳情頁；
  跨頁回覆層級會壓平（已知限制）

## GenericWebAdapter（web）說明

- `has_page_mode = True`，不攔截 HTTP responses，改用 `scrape_page()` 抓完整 HTML
- 支援 `--url URL`（單一頁面）或 `--url-list FILE`（文字檔，一行一個 URL，# 為註解）
- 結果存 `pages` 表（以 URL 為 primary key，upsert）
- `--output DIR` 同時將每頁寫成 `{slug}.md` 到指定目錄
- `horus export --format markdown` 可事後從 DB 批次匯出 .md 檔

## Serper Adapter 說明

- 走 Serper.dev 的 Google Search API 取得真・Google 搜尋結果，`has_http_mode = True`
  （用 `httpx` POST，**不需要 Playwright / browser**）
- **金鑰 env-only**：`HORUS_SERPER_API_KEY`（放 `.env` 或 export），**絕不經 CLI flag**
  （避免落入 `crawl_log` / serve API）。缺金鑰時錯誤訊息明確指向該 env var
- Endpoint：`POST https://google.serper.dev/search`，header `X-API-KEY`
- `--query`（必填，亦支援 stdin 多 query 一行一個）/ `--gl`（國別，預設 `tw`）/
  `--hl`（語系，預設 `zh-tw`）。預設 locale 對齊台灣瀏覽器 Google，避免回美國英文結果
- **credit 計費**：`num<=10` = 1 credit、`num` 11–100 = 2 credits。⚠️ crawl 的 top-level
  `--limit` 預設是 **50**，裸跑 `crawl serper --query X` 會請求 `num=50` → **每查吃 2 credits**。
  要省成本請帶 `--limit 10`（adapter 在 `num>10` 時也印 stderr 警告）
- **單次請求不跨頁**：拿單次 `num`（10–100），>100 直接 clamp 到 100
- extra 欄位：`query`, `rank`, `snippet`, `date`, `gl`, `hl`
- 結果品質可用 `src/horus/serp_eval.py`（overlap@k + RBO + `normalize_url`，純函式有單元測試）
  配合 `scripts/validate_serper.py`（gitignored 本機工具）比對「瀏覽器 Google vs serper」
