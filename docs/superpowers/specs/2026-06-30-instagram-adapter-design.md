# Instagram Adapter — Design Spec

Status: draft (brainstorm approved)
Date: 2026-06-30
Author: Peter

## 目標

新增 `instagram` site adapter，讓 `horus crawl instagram` 能爬取 IG profile 貼文與單一貼文，並用 `--with-comments` 抓貼文底下的**頂層留言**。走 GraphQL/XHR 攔截（鏡像 `threads` / `twitter`），不依賴 SSR。

## 範圍（brainstorm 已定）

| 決策 | 結論 |
|---|---|
| 爬取入口 | `--user @name`（profile 貼文）+ `--url <貼文>`（單貼），同 threads。**不做** hashtag |
| 留言深度 | **只抓頂層留言**，忽略 `child_comments`（巢狀回覆） |
| 媒體 | **只存媒體 URL**（`extra.media_urls`），不下載檔案 |
| 登入 | `requires_login=True`，`horus login instagram` 手動登入存 state（IG 無匿名存取） |

**YAGNI 砍掉**：hashtag、巢狀留言、媒體下載、stories/tagged/saved、留言分頁追到底。

## Step 0 偵察結果（2026-06-30，已完成，ground truth）

實機登入後攔截真實 response（recon harness：`scripts/recon_ig.py`，輸出在 gitignored `scripts/recon_ig_out/`）。

### Profile 貼文
- **Endpoint**：`POST https://www.instagram.com/graphql/query`
- **Body**：`data.xdt_api__v1__feed__user_timeline_graphql_connection`，含 `edges[]` + `page_info`
- **`edges[].node` 關鍵欄位**（與 `threads` node 同源）：
  ```
  pk: str                       # 貼文 id
  code: str                     # shortcode → url: https://www.instagram.com/p/<code>/
  media_type: int               # 1=IMAGE, 2=VIDEO, 8=CAROUSEL_ALBUM
  like_count: int
  comment_count: int
  taken_at: int                 # epoch → timestamp
  caption: {text, created_at, pk} | None
  user: {pk, username, ...}
  image_versions2.candidates[].url      # 圖片 URL（取 [0]）
  video_versions[].url                  # 影片 URL
  carousel_media[]                      # 輪播：每項自己的 image_versions2/video_versions
  ```
- **解析複用**：`threads._parse_item` / `_extract_media_urls` 的邏輯幾乎可直接搬（同 Meta 結構）。

### 單貼 media info（`--url` 模式的貼文來源）
- **Endpoint**：`GET https://www.instagram.com/api/v1/media/<media_id>/info/`
- **Body**：`items[]`，`items[0]` 的欄位 = 與 timeline node **同一組**（`pk/code/caption/taken_at/like_count/comment_count/media_type/user/image_versions2/carousel_media`，已實測確認）。
- → `--url <貼文>` 模式的貼文從這裡解析（profile 模式從 timeline graphql；兩者共用同一個 item builder）。

### 頂層留言
- **Endpoint**：`GET https://www.instagram.com/api/v1/media/<media_id>/comments/?can_support_threading=true&...`
- **Body 關鍵欄位**：
  ```
  comments[]                    # 頂層留言陣列
  comment_count: int
  has_more_comments: bool       # 實測首批為 False（單批已涵蓋）
  next_min_id: str (JSON-encoded cursor)
  caption.media_id: str         # = 貼文 pk
  ```
- **`comments[]` 每則**：
  ```
  pk: str
  media_id: str                 # = 貼文 pk（★ 每則都有，直接綁回貼文，不需外部傳 post_pk）
  text: str
  created_at: int               # epoch
  comment_like_count: int
  child_comment_count: int      # 有幾則回覆（我們不抓回覆，但記下數量）
  user: {pk, username, ...}
  ```
- **巢狀回覆**（不做）：`GET /api/v1/media/<id>/comments/<comment_id>/child_comments/`

### SSR 確認
profile 貼文（`xdt_api__v1__feed__user_timeline`）、留言（`edge_media_to_parent_comment`）在頁面 HTML 出現 **0 次** → **都不在 SSR**。確認必須走攔截；`threads` 的 HTML 留言解析路徑不適用。

## 架構

`InstagramAdapter(SiteAdapter)`，`requires_login=True`，走 `scrape()` 攔截路徑（`has_page_mode=False`、`has_http_mode=False`）。

### 1) 攔截過濾與解析（貼文 + 留言共用一條 `parse_response`）
- `get_response_filter()` **雙重過濾（URL pattern + body 結構，遵循 CLAUDE.md 慣例）**，比對三種 response：
  - **timeline 貼文**：`"graphql" in url` 且 body `data` 含鍵 `xdt_api__v1__feed__user_timeline_graphql_connection`
  - **單貼 media info**：url 含 `/api/v1/media/` 且 `/info/`，body 含 `items`
  - **頂層留言**：url 含 `/api/v1/media/` 且 `/comments/`，body 含 `comments` + `comment_count`
- `parse_response(body)` 依 body 結構 dispatch：
  - timeline connection → `edges[].node` → `_parse_item`（貼文）
  - media info → `items[]` → `_parse_item`（貼文）
  - comments → `comments[]` → `_parse_comment`（留言，用每則 body 的 `media_id` 綁回貼文）
- `get_urls(**kwargs)`：`--user @name` → `https://www.instagram.com/<name>/`；`--url` → 原樣；支援 stdin 多 user（同 threads）。

### 2) 留言：走既有 `scrape()` 攔截，core 改動極小
`media_id`（=貼文 pk）就在**每則留言的 body** 裡，留言可由標準 `parse_response` 直接解析並綁回貼文，**不需要新的 comment-parser 介面**。故放棄「攔截式 comment parser」構想（`scrape_comments_via_intercept` / `get_comment_response_filter` / `get_intercept_comment_parser` 都不做）。改為最小改動：

- **adapters/base.py**：`SiteAdapter` 新增 ClassVar 旗標 `comments_via_interception: ClassVar[bool] = False`（預設 False = 沿用既有 HTML 留言路徑）。`InstagramAdapter` 設 `True`。
- **cli.py `--with-comments` flow**（明確分支，threads 原封不動）。現況：`comment_parser = adapter.get_comment_parser() if with_comments else None`，並對 `crawled_top_level` 逐貼呼叫 `scraper.scrape_comments(url=post.url, post_pk=post.id, parser=comment_parser, ...)`（cli.py:300-325）。改為：
  ```python
  if with_comments and crawled_top_level:
      if getattr(adapter_cls, "comments_via_interception", False):
          # instagram：逐貼導航 post.url，用同一個 scrape() 攔截（filter 會命中 comments XHR）
          for post in crawled_top_level:
              await scraper.scrape(post.url, adapter.get_response_filter(),
                                   adapter.parse_response, state_path, on_batch=...)
      elif adapter.get_comment_parser():
          # threads：既有 HTML 路徑，不動
          for post in crawled_top_level:
              await scraper.scrape_comments(url=post.url, post_pk=post.id, ...)
  ```
  （確切變數/收尾以 cli.py 既有寫法為準；上面是分支結構規格。）
- 導航貼文頁時頁面自動發 `/api/v1/media/<id>/comments/`，`scrape()` 攔截 → `parse_response` 解析。
  - **留言的 `scrape()` 刻意不傳 `since=`**：留言有自己的 `created_at`，不可套貼文增量 cutoff（否則會默默丟掉舊留言）。
  - ⚠️ **要先驗證的執行假設**：「導航到 `/p/<code>/` 會在載入時自動發 comments XHR」是 recon harness 觀察到的行為，但尚未在 `scrape()` 的 `page.goto` 路徑上實證。**實作第一步就先驗這條**（首批留言能否攔到）；不成立則需在貼文頁觸發留言載入（點開留言區/滾動）再攔。
- **core/scraper.py 不需新介面**：`scrape()` / `scrape_comments()` 皆不動，IG 只是走既有 `scrape()` 那條。
> 設計理由（SOLID/YAGNI）：一個旗標宣告留言策略 + cli 明確二分支；threads 與 instagram 互不影響，也不為「拿得到的 post_pk」發明新介面。

### 3) Item 對應（IG 專屬解析，**不照搬 threads `_parse_item`**）
threads 的 `_parse_item`（threads.py:84-103）hard-code `site_id="threads"`、threads.net URL，且讀 IG **沒有**的 `text_post_app_info` → 不可照搬；只有 `_extract_media_urls` 結構相同可複用。
- **貼文 `_parse_item(node)`**：`id=pk`、`site_id="instagram"`、`url=https://www.instagram.com/p/<code>/`、`text=caption.text`、`author_id=user.pk`、`author_name=user.username`、`timestamp=taken_at`；extra：`like_count`/`comment_count`（**直接取 node**，非 text_post_app_info）、`media_type`（複用 `_MEDIA_TYPE_MAP`，IG 用 1/2/8）、`media_urls`（**複用** threads `_extract_media_urls` 邏輯）、`is_reply=False`、`parent_post_id=None`、`conversation_id=None`。
- **留言 `_parse_comment(comment)`**：`id=comment pk`、`site_id="instagram"`、`text=text`、`author_id=user.pk`、`author_name=user.username`、`timestamp=created_at`；extra：`comment_like_count`/`child_comment_count`、`is_reply=True`、`conversation_id=parent_post_id=media_id`、`media_type=None`、`media_urls=[]`。
  - **`url` 推導（重要，parse 時拿不到 shortcode）**：留言 body 只有 `media_id`、**沒有** `code`/permalink，故 `_parse_comment` 在 parse 時無法建出 `/p/<code>/`。決策：`_parse_comment` 先設 `url=""`（佔位），由 **cli.py 的逐貼攔截 loop 回填** `item.url = post.url`（該 loop 持有正在爬留言的 `post`，其 `post.url` 已是 `/p/<code>/`）。回填時亦可確認 `item.extra["parent_post_id"] == post.id`。`ScrapedItem.url` 為必填字串，`""` 為合法佔位、回填後即正確。

### 4) 註冊
`src/horus/adapters/__init__.py`：`from horus.adapters.instagram import InstagramAdapter` + `register(InstagramAdapter)`。`horus list-sites` 應出現 `instagram`。

## CLI / 行為

```bash
horus login instagram                              # 手動登入存 state
horus crawl instagram --user @name                 # profile 貼文（增量）
horus crawl instagram --user @name --with-comments # 連頂層留言
horus crawl instagram --url https://www.instagram.com/p/<code>/ --with-comments
```
- 增量：沿用 `get_latest_timestamp`（同其他 adapter）。
- 限流：沿用 `HORUS_SCROLL_DELAY_*` / `HORUS_REQUEST_JITTER`。
- **留言分頁（已知限制）**：MVP **只保證首批留言**（頁面載入即攔到，實測該貼 `has_more_comments=False`）。「滾動貼文頁是否會再觸發 `min_id` 分頁的 comments XHR」**尚未驗證**——`scrape()` 的滾動是為 feed 設計，套到貼文頁的留言載入是假設、非實測。若日後要抓滿，再另開 backlog 處理 cursor 分頁。

## 錯誤處理 / 防禦

- 解析全程 `.get` + 缺欄位 skip（IG 結構會變）。
- `taken_at` / `created_at` 缺 → skip 該筆。
- 未登入 / state 失效 → 沿用既有 `requires_login` 警告路徑。

## 測試（沿用 twitter adapter 慣例）

- 用 recon 抓到的**真實 response** 當 fixtures，置於 `tests/fixtures/instagram/`：
  - `user_timeline.json`（來自 `resp_005`）→ profile 貼文 parser 測試
  - `media_info.json`（來自 `resp_007`）→ `--url` 單貼 parser 測試
  - `media_comments.json`（來自 `resp_008`）→ 留言 parser 測試（驗證 `media_id` 綁回貼文）
  - ⚠️ **recon 檔有包一層 wrapper**（`{url, status, data_keys, body}`）。製作 fixture 時要取出 **`.body`** 存檔，否則 `parse_response` 的 dispatch（找頂層 `data`/`items`/`comments`）會全部 miss。
- **fixtures 消毒（強制，非選用）**：
  - 移除 `xdt_viewer`、`organic_tracking_token`、`*_tracking_token` 等 viewer/token 欄位。
  - **第三方留言者 PII 必須去識別**：`user` 的 `username`/`full_name`/`pk`/`fbid_v2`/`profile_pic_url` 一律換成假值、留言 `text` 換成中性測試字串。只留 2–3 則。**不可** commit 真實第三方帳號與留言內容。
- 純 parser 單元測試（無 Playwright、無 network）。`parse_response` 的三向 dispatch（timeline / media_info / comments）各一組測試；threads 既有測試須維持綠（回歸）。

## 風險

| 風險 | 嚴重度 | mitigation |
|---|---|---|
| 個人帳號被標記/封號 | **高** | 文件警告；建議分身帳號；保守限流 |
| IG GraphQL doc_id / 欄位改版 | 中 | 防禦性解析；fixtures 來自實機、可重抓更新 |
| 留言分頁被擋 | 中 | MVP 只抓首批+滾動幾批，不追到底 |
| fixtures 含敏感資料 | 中 | 消毒後才 commit |

## 完成標準

- [ ] `horus list-sites` 顯示 `instagram`
- [ ] `horus crawl instagram --user @name` 取得貼文存 DB（增量）
- [ ] `--with-comments` 抓到頂層留言，`is_reply`/`conversation_id`/`parent_post_id` 正確
- [ ] `--url <post>` 單貼可用
- [ ] threads 留言路徑不受影響（回歸測試綠）
- [ ] parser 測試用消毒後的真實 fixtures，CI 無 network
- [ ] CLAUDE.md + horus-scraping skill 同步更新
- [ ] `uv run pytest` / `ruff check` / `ty check` 全綠
