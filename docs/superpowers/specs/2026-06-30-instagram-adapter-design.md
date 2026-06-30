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

### 頂層留言
- **Endpoint**：`GET https://www.instagram.com/api/v1/media/<media_id>/comments/?can_support_threading=true&...`
- **Body 關鍵欄位**：
  ```
  comments[]                    # 頂層留言陣列
  comment_count: int
  has_more_comments: bool
  next_min_id: str (JSON-encoded cursor)
  ```
- **`comments[]` 每則**：
  ```
  pk: str
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

### 1) 貼文攔截
- `get_response_filter()`：`"graphql" in url` 且 body `data` 含鍵 `xdt_api__v1__feed__user_timeline_graphql_connection`。
- `parse_response(body)`：取該 connection 的 `edges[].node` → `_parse_item` → `ScrapedItem`。
- `get_urls(**kwargs)`：`--user @name` → `https://www.instagram.com/<name>/`；`--url` → 原樣；支援 stdin 多 user（同 threads）。

### 2) 留言攔截（唯一需要動 core）
留言是 XHR JSON（非 HTML），且需把留言綁回 post_pk（`parse_response` 只拿得到 body、拿不到 post_pk）。因此擴充 core 一條「攔截式留言」路徑，**平行於** threads 現有的 HTML `CommentParser`：

- **core/scraper.py**：
  - 既有 `CommentParser`（HTML）保留不動。
  - 新增「攔截式留言」能力：`scrape_comments_via_intercept(url, post_pk, comment_filter, comment_parser, state_path)` —— 導航到貼文頁、攔截符合 `comment_filter(url, body)` 的 response、把 **body + post_pk** 交給 `comment_parser(body, *, post_pk) -> list[ScrapedItem]`。
- **adapters/base.py**：`SiteAdapter` 新增宣告留言策略的鉤子（預設 None / HTML）：
  - `get_comment_response_filter() -> Callable[[str, dict], bool] | None`
  - `get_intercept_comment_parser() -> Callable[..., list[ScrapedItem]] | None`
  - 兩者皆非 None → CLI 的 `--with-comments` flow 走攔截路徑；否則沿用既有 HTML `get_comment_parser()`。
- **cli.py `--with-comments` flow**：依 adapter 宣告的策略二選一（HTML vs intercept）。threads 走 HTML（不動），instagram 走 intercept。
- **留言 → ScrapedItem**：`id=comment pk`、`is_reply=True`、`conversation_id=parent_post_id=post_pk`、extra 存 `comment_like_count` / `child_comment_count` / `media_type=None`。

> 設計理由（SOLID）：adapter 宣告自己的留言策略，core 提供兩條可選機制，threads/instagram 各取所需、互不影響（OCP + 策略模式）。

### 3) ScrapedItem 對應
- 貼文 `id=pk`、`url=https://www.instagram.com/p/<code>/`、`text=caption.text`、`author_id=user.pk`、`author_name=user.username`、`timestamp=taken_at`。
- extra：`like_count / comment_count / media_type / media_urls / is_reply=False / parent_post_id=None / conversation_id=None`。

## CLI / 行為

```bash
horus login instagram                              # 手動登入存 state
horus crawl instagram --user @name                 # profile 貼文（增量）
horus crawl instagram --user @name --with-comments # 連頂層留言
horus crawl instagram --url https://www.instagram.com/p/<code>/ --with-comments
```
- 增量：沿用 `get_latest_timestamp`（同其他 adapter）。
- 限流：沿用 `HORUS_SCROLL_DELAY_*` / `HORUS_REQUEST_JITTER`；留言滾動保守，只抓首批 + 滾動載入的幾批（不追 `min_id` 分頁到底）。

## 錯誤處理 / 防禦

- 解析全程 `.get` + 缺欄位 skip（IG 結構會變）。
- `taken_at` / `created_at` 缺 → skip 該筆。
- 未登入 / state 失效 → 沿用既有 `requires_login` 警告路徑。

## 測試（沿用 twitter adapter 慣例）

- 用 recon 抓到的**真實 response** 當 fixtures，置於 `tests/fixtures/instagram/`：
  - `user_timeline.json`（來自 `resp_005`）→ 貼文 parser 測試
  - `media_comments.json`（來自 `resp_008`）→ 留言 parser 測試
- **fixtures 需消毒**：移除 `xdt_viewer`、`organic_tracking_token` 等 viewer/token 欄位，留言只留少數幾則、用去識別或公開帳號樣本（避免 commit 私人/敏感資料）。
- 純 parser 單元測試（無 Playwright、無 network）；core 的攔截留言路徑用既有 scraper 測試慣例。

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
