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
├── cli.py              # Click CLI（login, crawl, list-sites, show, search, export, stats）
├── config.py           # Settings（HORUS_* env vars，~/.horus/ 路徑管理）
├── models.py           # ScrapedItem, CrawlResult, SiteAdapterConfig
├── core/
│   ├── browser.py      # BaseBrowser（Playwright 生命週期，save_login_state）
│   ├── scraper.py      # BaseScraper（scrape：scroll + response intercept）
│   └── storage.py      # HorusStorage（SQLite + FTS5）
└── adapters/
    ├── base.py         # SiteAdapter ABC（3 abstract methods）
    ├── __init__.py     # Registry（register, get_adapter, list_adapters）
    └── threads.py      # Threads adapter
tests/
├── conftest.py         # storage fixture（in-memory SQLite）
├── test_storage.py
└── adapters/
    └── test_threads_adapter.py
```

## 常用指令

```bash
horus login threads                              # 開 browser 手動登入，儲存 ~/.horus/states/threads.json
horus crawl threads --user @username             # 爬取貼文（增量）
horus crawl threads --user @username --mode replies  # 爬取回覆
horus crawl threads --url https://...            # 爬特定 URL
horus list-sites                                 # 列出可用 adapters
horus show --site threads --limit 20             # 顯示已儲存資料
horus search "關鍵字" --site threads             # FTS 搜尋（支援中文）
horus export --site threads --format json -o out.json
horus export --site threads --format csv -o out.csv
horus stats                                      # 統計資訊
```

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

## 環境變數

```
HORUS_BASE_DIR=~/.horus        # 資料根目錄
HORUS_DB_PATH=~/.horus/data.db # SQLite 路徑（覆蓋預設）
HORUS_HEADLESS=true            # headless mode
HORUS_SCROLL_DELAY_MIN=3.0
HORUS_SCROLL_DELAY_MAX=8.0
HORUS_REQUEST_JITTER=2.0
HORUS_MAX_PAGES=50
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
