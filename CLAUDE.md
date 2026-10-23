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
├── cli.py              # Click CLI（login, crawl, list-sites, show, search, pages, export, stats, serve, mcp）；crawl 是 CrawlOrchestrator 的 thin shell
├── config.py           # Settings（HORUS_* env vars，~/.horus/ 路徑管理）
├── models.py           # ScrapedItem, ScrapedPage, CrawlResult, SiteAdapterConfig
├── serp_eval.py        # 純函式 SERP 比對指標（normalize_url, overlap_at_k, rbo）
├── core/
│   ├── browser.py      # BaseBrowser（Playwright 生命週期，save_login_state）
│   ├── scraper.py      # BaseScraper（scrape：scroll + response intercept；scrape_page：HTML → Markdown）
│   ├── orchestrator.py # CrawlOrchestrator（http/page/intercept 三向 dispatch + 儲存副作用；cli/serve/mcp 共用）
│   ├── crawl_plan.py   # resolve_crawl_plan（adapter → urls → 登入狀態；cli/serve/mcp 使用）
│   ├── jobs.py         # JobManager（背景爬取工作；serve 的 CrawlerManager 包在外層只做 SSE；serve/mcp 使用）
│   ├── dates.py        # parse_since（cli/serve/mcp 共用，raises ValueError）
│   └── storage.py      # HorusStorage（SQLite + FTS5；items + pages 兩張表）
├── mcp/                # `horus mcp`：stdio MCP server（官方 SDK v2 MCPServer）
│   ├── server.py       # build_server / run_stdio（disable_stdin → 跑 stdio → finally 取消背景工作）
│   ├── state.py        # McpState（共用 storage + JobManager + 同站一次一個）、job_payload
│   ├── tools_query.py  # list_sites, search, show, get_item, pages, get_page, stats
│   ├── tools_crawl.py  # crawl（等 wait_seconds 後轉背景）, crawl_status, crawl_cancel
│   └── formatting.py   # model → JSON dict（文字預覽截斷、crawl 參數改名）
└── adapters/
    ├── base.py         # SiteAdapter ABC（mode flags + page-mode hooks + fetch_comments() hook；get_urls 唯一 abstract）
    ├── __init__.py     # Registry（register, get_adapter, list_adapters）
    ├── _io.py          # 共用 I/O helpers（read_stdin_lines, stable_item_id）
    ├── _meta.py        # 共用 Meta 媒體解析（_MEDIA_TYPE_MAP, _extract_media_urls；threads/instagram 共用）
    ├── threads.py      # Threads adapter（GraphQL 攔截）
    ├── twitter.py      # Twitter/X adapter（GraphQL 攔截，anonymous 可用）
    ├── instagram.py    # Instagram adapter（GraphQL/XHR 攔截，貼文 + 頂層留言，fetch_comments() override（profile-grid modal walk））
    ├── web.py          # GenericWebAdapter（任意公開網頁 → Markdown，has_page_mode=True）
    └── serper.py       # Serper adapter（Google Search via httpx，has_http_mode=True）
tests/
├── conftest.py         # storage fixture（in-memory SQLite）
├── test_storage.py
├── fixtures/twitter/   # 真實 X GraphQL response（parser 測試用）
├── fixtures/instagram/ # 消毒後 IG response（timeline/media_info/comments）
└── adapters/
    ├── test_threads_adapter.py
    ├── test_twitter_adapter.py
    ├── test_web_adapter.py
    ├── test_instagram_adapter.py  # parser/filter/get_urls（用消毒 fixtures）
    └── test_instagram_cli.py      # cli 攔截留言分支 + url 回填
└── mcp_server/         # MCP 工具測試（in-memory Client）+ stdio 煙霧測試；不叫 mcp/ 以免遮住 SDK
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
horus login instagram                            # 開 browser 手動登入（建議分身帳號），存 states/instagram.json
horus crawl instagram --user @nasa               # 爬 IG profile 貼文（需登入）
horus crawl instagram --user @nasa --with-comments  # 貼文 + grid 首頁各貼的頂層留言（modal walk）
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
horus mcp                                        # stdio MCP server，給 agent 用
```

## 開發規範

- 新增或修改 adapter 後，**必須更新** `~/.claude/skills/horus-scraping/SKILL.md`，確保 skill 與實作同步。
- 使用 `horus-scraping` skill 作為開發新 adapter 的參考指南。

## 新增 Site Adapter（3 步驟）

1. 建立 `src/horus/adapters/mysite.py`，繼承 `SiteAdapter`，實作 `get_urls()`（唯一 abstract）；
   interception adapter 另覆寫兩個 default method：
   - `get_urls(**kwargs)` → `list[str]`：根據 CLI 參數產生 URL 列表（**abstract，必須實作**）
   - `get_response_filter()` → `(url, body) -> bool`：過濾攔截的 HTTP response（default 全拒）
   - `parse_response(body)` → `list[ScrapedItem]`：解析成通用格式（default 回空 list）

2. 在 `src/horus/adapters/__init__.py` 加：
   ```python
   from horus.adapters.mysite import MySiteAdapter
   register(MySiteAdapter)
   ```

3. 執行 `horus list-sites` 確認出現

## 關鍵設計

- **CrawlOrchestrator**：`core/orchestrator.py` 是 crawl 編排的單一實作（http / page / intercept
  三向 dispatch、儲存副作用、留言抓取）；cli、serve、mcp 都是 thin shell，只差 `CrawlReporter` 實作
  （Rich console + stdout JSONL vs SSE broadcast + job log；mcp 走 JobManager 的 job log）
- **`--limit` 是每個目標的上限**：每個 profile / 貼文 URL 各自最多約 N 筆；`0` = 不限。http 模式傳給
  `fetch_items`；攔截模式捲動到該目標收集滿 N 筆（去重、比 since 新）就停；page 模式
  （web/facebook）不受影響，一個 URL 一頁。是停止門檻不是精確上限，最後一批整批保留。
  預設 50，所以捲動爬取每個 profile 約 50 筆就停，要抓完整 profile 請給 `--limit 0`。
  serve 網頁表單只收 1–1000，`POST /crawl` API 才接受 0（不限）；MCP `crawl` 工具預設 10，接受 0
- **MCP server（`horus mcp`）**：只提供 tools（無 resources/prompts）；`crawl` 等到 `wait_seconds`
  （預設 45、上限 600）還沒完成就回 `job_id` 轉背景，agent 用 `crawl_status` 輪詢；同一站一次只跑
  一個（MCP 專屬政策，serve 不套用）；不做登入，缺登入狀態時要使用者在終端跑 `horus login <site>`。
  stdio 紀律：stdin 由 `disable_stdin()` 關閉（adapter 不會把協定訊息當 URL 清單讀）；MCP 路徑上
  沒有任何輸出寫到 stdout，stdout 只放協定訊息（只有 login 會 print 到 stdout，MCP 不開放 login）。
  背景工作跟著 server 程序，agent 關掉就中斷
- **ScrapedItem.extra**：site-specific 欄位存 JSON dict，避免多表 migration
- **response_filter 簽名**：`(url: str, body: dict) -> bool`，雙重過濾（URL pattern + body 結構）
- **State 儲存**：`~/.horus/states/<site_id>.json`，per-site Playwright storage_state
- **FTS5 trigram**：支援中文搜尋，短查詢（<3字）自動 fallback LIKE
- **WAL mode**：SQLite write-ahead logging
- **增量爬取**：沒給 `--since` 時，profile 網址只抓「上次爬這個網址（且有抓到資料）的開始時間」之後的貼文
  （`SiteAdapter.is_profile_url(url)` → `crawl_log` 的 `get_last_crawl_start(site, url)`）。以網址為準、不看作者：
  轉推與回覆串原文會記在別人名下，看作者會擋掉那些帳號的第一次爬取。posts／replies 分頁各自計算；
  從沒爬過的網址不設起點。`crawl_log` 每個網址記自己的筆數。單貼、討論串等非 profile 網址不自動設起點。有給 `--since` 時捲動抓取的網址都用它
  （threads / instagram 的單貼網址走 SSR-JSON，不套用 since）
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
- **`--url` 單貼走 SSR-JSON**（同 IG 模式）：threads 貼文頁把 thread inline 成 SSR
  `thread_items` array、不 fire mediaData XHR。`is_post_url()` match `/@user/post/<code>`，
  `extract_inline_items()` 用 `_extract_thread_items_arrays` 取第一個 array（貼文所在 thread）
  合成 mediaData 形狀，`parse_response` 原樣處理（單貼=posts mode、中段回覆=replies mode 帶連結）
- `--with-comments` 留言走 `parse_comments_from_html`（同一套 SSR array 解析，跳過第一個 array）
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

## Instagram Adapter 說明

- 走 response 攔截（同 Threads/Twitter，`has_page_mode=False`），攔截三種 endpoint：
  - graphql timeline connection（`xdt_api__v1__feed__user_timeline_graphql_connection`）→ profile 貼文
  - `/api/v1/media/<id>/info/`（body 含 `items`）→ modal 開啟時的單貼資料
  - `/api/v1/media/<id>/comments/`（body 含 `comments`）→ 頂層留言
- **需登入**：`requires_login=True`，先 `horus login instagram`（**建議用分身帳號**，爬取有封號風險）。
  無 anonymous 模式。
- **兩種抓取模式**（`is_post_url()` 判斷 URL 屬單貼還是 profile）：
  - `--user @name`（profile 貼文）/ stdin 多 user（一行一個）→ 走 timeline XHR 攔截。
  - `--url <post>`（`/p/<code>/` 或 `/reel/<code>/`）→ 直接導航不 fire 可攔截 XHR，改走 **SSR-JSON**：
    `extract_inline_items()` 從頁面 `<script type=application/json>` 抽出 inline 的
    `xdt_api__v1__media__shortcode__web_info`（`{items:[...]}` 節點），交給 `parse_response` 的 items 分支。
    orchestrator 對 interception adapter 的 post URL 走 `scraper.fetch_inline_json()`，非 post URL 走 `scrape()`。
- **`--with-comments`：只抓頂層留言**（不抓 reply 的 reply）。留言只存媒體 URL（同 threads）。
- **留言擷取機制（`fetch_comments()` override（profile-grid modal walk），實機驗證過）**：IG 留言只在「從 profile grid
  **點縮圖開 in-app modal**」時才 fire `/api/v1/media/<id>/comments/`；直接導航貼文頁**不會** fire。因此：
  - 主爬 `on_batch` **一律丟棄** `url=""` 的 `is_reply` 項目（即使沒帶 `--with-comments`），防止留言誤落地。
  - `--with-comments` 時，adapter 的 `fetch_comments()` 依貼文 `author_name` 推出 profile URL，呼叫
    `scraper.scrape_comments_via_grid(profile_url, ...)`：載入 profile → 開 modal → 攔留言，
    再以 `parent_post_id == 貼文 pk` 歸屬（杜絕跨貼文誤掛）、回填 `url = 貼文 URL` 後才存。
    - `--user` 模式：`target_codes=None` → 點 grid 首屏前 N 個縮圖（依序，近期貼文）。
    - `--url` 模式：`target_codes=[該貼 code]` → 捲動 grid 找到該貼**專屬**縮圖再點（精準抓指定貼文）。
- 關鍵解析：貼文 `_parse_item(node)` 用 `pk`/`code`/`taken_at`/`caption.text`/`user`，`like_count`/
  `comment_count` **直接取 node**（非 threads 的 `text_post_app_info`）；`media_urls` 複用 `_meta._extract_media_urls`。
  留言 `_parse_comment` 以 body 內 `media_id`（= 貼文 `pk`，裸值無 `_ownerid` 後綴）綁回貼文。
- extra 欄位（貼文）：`like_count`, `comment_count`, `media_type`, `media_urls`, `is_reply=False`,
  `parent_post_id=None`, `conversation_id=None`
- extra 欄位（留言）：`comment_like_count`（**注意**：留言用此鍵，非貼文的 `like_count`）,
  `child_comment_count`, `is_reply=True`, `parent_post_id`/`conversation_id`（= media_id）,
  `media_type=None`, `media_urls=[]`
- 已知限制：
  - **`--url` 單貼留言只到首批**：SSR-JSON 拿完整貼文；`--with-comments` 走精準 modal，只攔 modal
    開啟當下的第一批（~15 則）頂層留言，留言分頁 cursor 未實作。若該貼太舊、捲動 grid 20 次仍找不到
    縮圖則跳過留言（貼文仍會存）。
  - **`--user` 留言只涵蓋 grid 首頁貼文的首批留言**：modal walk 只點 profile grid 首屏載入的縮圖
    （~9–12 篇），grid 未捲動載入更多；每篇也只攔到第一批（~15 則）頂層留言。
  - **carousel 影片 URL 未抓**：`_extract_media_urls` 對 carousel 只取圖片 candidates，carousel 內
    `video_versions` 未展開（單則影片貼文的 `video_versions` 有抓）。
  - 缺 `code` 的貼文 `url` 會是 `https://www.instagram.com/p//`（保留 item 讓 caller 判斷，不丟棄）。
  - stdin 多 user 不濾 `#` 註解行（與 threads 一致）。
  - 帳號封號風險 → 建議分身帳號。

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
