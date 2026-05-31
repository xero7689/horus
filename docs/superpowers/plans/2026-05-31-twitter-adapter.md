# Plan: Twitter (X) Adapter — GraphQL response interception

Status: revised (post external review — Claude Code adversarial pass, 2 reviewers)
Author: Peter
Date: 2026-05-31
Revised: 2026-05-31 (incorporated review findings — 4 HIGH, 3 MED, 2 LOW)
US: US-004

## Review changelog（修了什麼）

| # | Sev | 問題 | 修正 |
|---|---|---|---|
| 1 | HIGH | 只處理 `TimelineAddEntries` → 置頂貼文漏抓 | `_extract_entries` 加 `TimelinePinEntry`，dedup |
| 2 | HIGH | RT 外層 full_text 截斷、author 變轉推者 | unwrap `retweeted_status_result`，內層取 text/author/media；外層保留 id+timestamp |
| 3 | HIGH | parser crash 會中斷整個 crawl（scraper 不 guard） | per-entry try/except 回 None；全程 `.get()` 不用 `[...]` |
| 4 | HIGH | `in_reply_to` 串鏈懸空、樹壓平 | 改用 Threads 的 positional `prev_id` 串鏈；root `is_reply=False` |
| 5 | MED | `"user" in data` 假過濾 | body check 改抓 `timeline` 結構 |
| 6 | MED | tombstone/visibility 分支無 fixture 支撐 | 降為「防禦式 skip」，不宣稱已測；replies 階段補 TweetDetail fixture |
| 7 | MED | video HLS variant 無 bitrate → KeyError | 先 filter `video/mp4` 再取 max bitrate |
| 8 | LOW | fixture 標 182KB（實際 570KB） | 更正為 ~570KB（583,785 bytes） |
| 9 | LOW | `--with-comments` 對 twitter 是 no-op | 文件註明，不支援 |

## 0. 偵察結果（2026-05-31 實測 x.com/NASA，anonymous headless）

**這份是實打實抓的，不是猜的。** Capture 已存 `tests/fixtures/twitter/user_tweets.json`（**~570KB / 583,785 bytes** 真實 response，當 parser fixture 用）。

### 0.1 關鍵發現

| 發現 | 影響 |
|---|---|
| **Anonymous（未登入）可拿到公開 profile 的 `UserTweets` 200** | `requires_login = False` 可行，**推翻 US-004 草稿假設的「需登入」** |
| 但 anonymous 用 guest token，X 對其 **限流很兇**（少量 request 後就 429 / 撤 token） | 量大時建議 `horus login twitter` 存 state；adapter 兩種都支援（`state_path` 自動 fallback `None`） |
| Author 欄位已從 `legacy.screen_name` 搬到 `core.user_results.result.core` | **舊版 twscrape/nitter parser 抄過來會直接壞**（legacy 現在回 `null`）。必須走新路徑 |
| 走 response 攔截，基礎建設與 Threads 完全相同 | `has_page_mode=False` + `has_http_mode=False` → `scrape()` 路徑，零新基礎建設 |

### 0.2 GraphQL operations（依導航頁面自動觸發）

| 導航 URL | 觸發 operation | 用途 |
|---|---|---|
| `x.com/<user>` | `UserTweets` | posts tab（預設） |
| `x.com/<user>/with_replies` | `UserTweetsAndReplies` | replies mode |
| `x.com/<user>/status/<id>` | `TweetDetail` | 單一貼文 + 整串（`--url`） |

URL 形如 `/i/api/graphql/<hash>/UserTweets?variables=...`。Scroll 會觸發新的 `UserTweets` XHR（cursor 分頁），跟 Threads 一樣靠 `scrape()` 的 scroll loop 自動翻頁（`scrape()` 的「連續 3 次無新 item 就停」guard 保證最壞情況是提早結束、不是 hang）。

### 0.3 Response 結構（UserTweets / UserTweetsAndReplies）

```
data.user.result.timeline.timeline.instructions[]
```

**⚠️ instructions 有 4 種 type（實測），不是只有一種：**

| instruction type | 處理 |
|---|---|
| `TimelineClearCache` | skip |
| `TimelinePinEntry` | **要處理！** 置頂貼文在這，單一 `entry`（非 `entries[]`）→ `entry.content.itemContent.tweet_results.result` |
| `TimelineAddEntries` | 主時間軸，`entries[]` |
| `TimelineTerminateTimeline` | skip |

（TweetDetail 根路徑不同：`data.threaded_conversation_with_injections_v2.instructions[]`）

**Entry 類型（看 entryId prefix）：**

| entryId prefix | content.entryType | 內容 |
|---|---|---|
| `tweet-<id>` | `TimelineTimelineItem` | 單一貼文 → `content.itemContent.tweet_results.result` |
| `profile-conversation-<id>` | `TimelineTimelineModule` | 自串（self-thread）→ `content.items[]`，每個 `item.item.itemContent.tweet_results.result` |
| `cursor-top/bottom-<id>` | — | 分頁游標，忽略 |
| 其他（`who-to-follow-*`, `promoted-tweet-*`, `tweetdetailrelatedtweets-*` 等） | — | **預設 skip**（白名單只收上面三種，其餘一律忽略） |

### 0.4 `tweet_results.result` schema（實測欄位）

```jsonc
{
  "__typename": "Tweet",          // 也可能是 "TweetWithVisibilityResults"（本 fixture 無）→ unwrap .tweet；未知 typename → 回 None skip
  "rest_id": "2060549954846814442",
  "core": {
    "user_results": { "result": {
      "rest_id": "11348282",
      "core": { "name": "NASA", "screen_name": "NASA" },   // ← author 在這！legacy 已空
      "legacy": { /* screen_name/name 現在是 null */ }
    }}
  },
  "legacy": {
    "full_text": "...",            // ≤280 字；超過看 note_tweet；RT 時這裡是「RT @user: …」截斷版
    "created_at": "Sat May 30 02:32:47 +0000 2026",  // strptime "%a %b %d %H:%M:%S %z %Y" → tz-aware
    "favorite_count": 0, "retweet_count": 1813, "reply_count": 0,
    "quote_count": 0, "bookmark_count": 0, "lang": "en",
    "id_str": "2060549954846814442",
    "conversation_id_str": "2060549954846814442",
    "in_reply_to_status_id_str": null,
    "in_reply_to_screen_name": null,
    "is_quote_status": false,
    "entities": { "media": [ ... ] },
    "extended_entities": { "media": [ ... ] },           // 優先用這個取 media
    "retweeted_status_result": { "result": { /* 轉推時真正內容在這（含完整 full_text/note_tweet/media/原作者） */ } }
  },
  "note_tweet": { "note_tweet_results": { "result": { "text": "..." } } },  // >280 字完整長文
  "views": { "count": "106", "state": "EnabledWithCount" }
}
```

**Media（`extended_entities.media[]` 優先，fallback `entities.media[]`）：**
- `type`: `photo` / `video` / `animated_gif`
- `media_url_https`: 圖片 URL
- video/gif：`video_info.variants[]` — **先過濾 `content_type == "video/mp4"`，再取 `bitrate` 最高**。⚠️ variants 含一個 `application/x-mpegURL`（HLS）entry **沒有 `bitrate` key**，不先過濾就 `v["bitrate"]` 會 KeyError。

## 1. 目標

```bash
horus login twitter                                  # 選用，存 state 提升穩定度
horus crawl twitter --user @NASA                     # 爬貼文（含置頂）
horus crawl twitter --user @NASA --mode replies      # 爬回覆
horus crawl twitter --url https://x.com/NASA/status/123  # 單串
horus list-sites                                     # 顯示 twitter
```

存進 `items` 表，與 `horus search` / `show` / `export` / 前端詳情頁（US-003）統一查詢。

## 2. 實作計畫

### 2.1 新檔案 `src/horus/adapters/twitter.py`

```python
class TwitterAdapter(SiteAdapter):
    site_id = "twitter"
    display_name = "Twitter / X (x.com)"
    login_url = "https://x.com/login"
    requires_login = False   # anonymous 可跑；登入更穩
    description = "Scrape posts and replies from X via GraphQL interception"
```

### 2.2 `get_response_filter()`

```python
def filter_fn(url: str, body: dict) -> bool:
    if "graphql" not in url:
        return False
    if not any(op in url for op in ("UserTweets", "UserTweetsAndReplies", "TweetDetail")):
        return False
    data = body.get("data", {})
    # 真結構過濾（修 finding #5：不能只看 "user" in data，UserByScreenName 也有）
    user_result = data.get("user", {}).get("result", {})
    return "timeline" in user_result or "threaded_conversation_with_injections_v2" in data
```

### 2.3 `parse_response(body)` — 核心工作

**鐵則（修 finding #3）：`parse_response` 絕對不可 raise。** `core/scraper.py:98,127` 直接 `parser(resp)` 無 try/except，任何 exception 會中斷整個 crawl。每個 entry 包 try/except，失敗回 `None` 並 `continue`；一律 `.get()` 鏈，不用 `[...]`。

Helper：

| Helper | 行為 |
|---|---|
| `_extract_entries(body)` | 走 `instructions`：`TimelineAddEntries` → `entries[]`；`TimelinePinEntry` → 包成單 entry（修 #1）；相容 TweetDetail 根路徑；其餘 instruction type skip |
| `_unwrap_tweet(result)` | `result.get("__typename")`：`"Tweet"` 回自身；`"TweetWithVisibilityResults"` 回 `result.get("tweet")`；其他/空 → `None`（防禦式，修 #6） |
| `_parse_tweet(result, *, parent_id, conversation_id, is_reply)` | 單一 tweet → `ScrapedItem`；含新 author 路徑、note_tweet、media、**RT unwrap** |
| `parse_response(body)` | entry 白名單分派；`tweet-*`/pinned → 一筆；`profile-conversation-*` → 走 `items[]` **positional 串鏈**；其餘 prefix → skip；最後依 `id` dedup（pinned 可能也出現在 AddEntries） |

**RT 處理（修 finding #2）：** `_parse_tweet` 偵測 `legacy.get("retweeted_status_result", {}).get("result")`，有就 unwrap 內層 tweet，**從內層取 `text`（note_tweet/full_text）、author、media**；但 `id`/`timestamp` 用**外層**（保 dedup 與 `since` 增量正確，外層 RT timestamp 單調）。`extra` 記 `is_retweet=True` 與 `retweet_of_author`（原作者）。

**self-thread / replies 串鏈（修 finding #4）：** 沿用 **Threads `_parse_replies` 的 positional `prev_id` 串鏈**，不用 `in_reply_to_status_id_str`（實測 parent 常不在 response 裡 → 懸空）。module 內：第一筆 `is_reply=False`、`parent_post_id=None`、`conversation_id=conversation_id_str`（當作 chain root）；後續 `is_reply=True`、`parent_post_id=prev_id`。`extra` 慣例（`conversation_id`/`parent_post_id`/`is_reply`）與 US-002/US-003 一致，既有 `thread_tree`、前端詳情頁免改。

**已知限制（v1 接受）：** 回覆鏈若 parent 是 response 外的貼文，`thread_tree._resolve_parent` 會 fallback 到 conversation root → 樹**壓平成兩層**，不中斷、不爆，但非完整層級。完整層級需 TweetDetail 補抓，列後續。

### 2.4 `ScrapedItem` 對應

```python
src = _unwrap_retweet(result)          # RT → 內層；否則自身（修 #2）
leg = src.get("legacy", {})
outer_leg = result.get("legacy", {})   # id/timestamp 用外層
author = result["core"]["user_results"]["result"].get("core", {})  # 全程 .get（修 #3）
note = src.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})

ScrapedItem(
    id=outer_leg.get("id_str"),
    site_id="twitter",
    url=f"https://x.com/{author.get('screen_name')}/status/{outer_leg.get('id_str')}",
    text=note.get("text") or leg.get("full_text"),       # 長文/RT 內層優先
    author_id=result["core"]["user_results"]["result"].get("rest_id"),
    author_name=author.get("screen_name"),
    timestamp=_parse_x_time(outer_leg.get("created_at")),  # 外層 RT 時間
    extra={
        "like_count": leg.get("favorite_count", 0),
        "retweet_count": leg.get("retweet_count", 0),
        "reply_count": leg.get("reply_count", 0),
        "quote_count": leg.get("quote_count", 0),
        "bookmark_count": leg.get("bookmark_count", 0),
        "view_count": int(src.get("views", {}).get("count")) if str(src.get("views", {}).get("count","")).isdigit() else None,
        "media_type": media_type,            # photo/video/animated_gif/None
        "media_urls": media_urls,            # video 先 filter mp4 再取 max bitrate（修 #7）
        "lang": leg.get("lang"),
        "is_reply": is_reply,
        "is_retweet": result.get("legacy", {}).get("retweeted_status_result") is not None,
        "retweet_of_author": rt_author,      # RT 原作者；非 RT 為 None
        "is_quote": leg.get("is_quote_status", False),
        "parent_post_id": parent_id,         # positional prev_id（非 in_reply_to）
        "conversation_id": leg.get("conversation_id_str"),
        "reply_to_username": leg.get("in_reply_to_screen_name"),
    },
)
```

### 2.5 `get_urls(**kwargs)`

照 Threads：`--url` → `[url]`；`--user`+`mode` → `x.com/<user>` 或 `x.com/<user>/with_replies`；stdin 多 user；都無 → `ValueError`。strip `@`。（`--mode` 經 `_parse_extra_args` → `get_urls(**kwargs)`，已驗證 plumbing 同 Threads。）

### 2.6 `get_crawl_options()`

```python
[{"name": "--user", "help": "X username (e.g. @NASA)", "required": False, "default": None},
 {"name": "--mode", "help": "posts or replies", "required": False, "default": "posts"}]
```

不宣告 `--limit`（finding #7 確認：`--limit` 被 Click top-level 吃掉、不會傳到 adapter；量由 settings `max_pages` 控）。

### 2.7 註冊

`adapters/__init__.py`：`from horus.adapters.twitter import TwitterAdapter` + `register(TwitterAdapter)`。

### 2.8 login（零改動）

`horus login twitter` 走現有泛用 `_login` → `save_login_state(login_url, ...)`，已驗證完全 generic。文件註明「選用，量大才需要」。

## 3. 測試 — 真實 fixture 打 parser

`pytest-recording` 未安裝且 parser 是純函式，VCR 過頭。直接用偵察存下的真實 response：`tests/fixtures/twitter/user_tweets.json`。

**誠實標註覆蓋（修 finding #6）：** 本 fixture 全是 `__typename: "Tweet"`，**無** tombstone / `TweetWithVisibilityResults`。那兩個分支只做**防禦式 skip**（`_unwrap_tweet` 回 None），**不宣稱已測**。實作 replies/`--url` 時順手抓一份 `TweetDetail` fixture（可能含 visibility-gated / tombstone），屆時再補測。

| Test | 目的 | fixture 支撐 |
|---|---|---|
| `test_parse_user_tweets_fixture` | 真 fixture → 正確筆數（含置頂），cursor/who-to-follow 不入列 | ✅ |
| `test_pinned_tweet_included` | `TimelinePinEntry` 的置頂貼文有被收（修 #1） | ✅ |
| `test_author_uses_core_path` | screen_name 來自 `core.core`，非 null legacy | ✅ |
| `test_note_tweet_overrides_full_text` | >280 長文取 note_tweet | ✅（286 字那則） |
| `test_retweet_unwraps_full_text` | RT 取內層完整 text + 原作者，非 `"RT @…"` 截斷（修 #2） | ✅（6 則 RT） |
| `test_retweet_keeps_outer_id_timestamp` | RT 用外層 id/timestamp（dedup/增量） | ✅ |
| `test_self_thread_positional_linking` | profile-conversation positional 串鏈、root `is_reply=False`（修 #4） | ✅ |
| `test_media_video_picks_max_mp4_bitrate` | 跳過 HLS、取最高 mp4（修 #7） | ✅（3 video） |
| `test_parse_never_raises_on_unknown_entry` | 餵畸形 entry → skip 不 raise（修 #3） | ✅（合成 entry，測 robustness 非 schema） |
| `test_created_at_parsing` | X 時間 → aware datetime | ✅ |

## 4. v1 Scope（YAGNI）

**做**：`--user`/`--url`/`--mode posts|replies`、stdin 多 user、anonymous + 選用 login state、置頂貼文、self-thread/replies positional 串鏈（接 thread_tree）、media（photo+video，跳 HLS）、RT unwrap、quote 標記（不展開被引內容）、增量爬取（`since`）。

**砍到後續**：Search/hashtag timeline（`SearchTimeline`）、quoted tweet 巢狀內容展開、List/Bookmarks timeline、完整跨頁回覆層級（TweetDetail 補抓）、影片下載（只存 URL）、`--with-comments`（X 無 SSR thread_items 等價物，回覆走 `--mode replies`）。

## 5. 風險

| 風險 | 嚴重度 | mitigation |
|---|---|---|
| **X schema 再變**（author 已搬過一次、instruction 多型） | 高 | 全程 `.get()` 鏈、per-entry try/except 回 None（finding #3）；fixture drift test 抓變動 |
| Anonymous guest token 限流 / 撤銷 | 中 | 文件導引 `horus login twitter`；既有 scroll jitter 降速 |
| Bot detection（X 比 Threads 兇） | 中 | 沿用既有 UA + viewport + jitter；headed login 拿 cookie |
| 跨頁回覆樹壓平 | 低 | v1 接受（§2.3 已述）；後續 TweetDetail 補完整層級 |
| tombstone/visibility 分支未實測 | 低 | 防禦式 skip 不爆；replies 階段補 TweetDetail fixture（finding #6） |

## 6. 完成標準

- [ ] `horus list-sites` 顯示 `twitter`
- [ ] `horus crawl twitter --user @NASA` anonymous 取得貼文（**含置頂**）並存 DB
- [ ] RT 存完整內層 text + 原作者，非截斷
- [ ] `--mode replies` 與 `--url <status>` 正常
- [ ] self-thread 在前端詳情頁（US-003）呈現樹狀（跨頁層級壓平為已知限制）
- [ ] parser test 跑真實 fixture，含 `test_parse_never_raises_on_unknown_entry`，CI 無 network call
- [ ] `uv run pytest`、`uv run ruff check`、`uv run ty check src/` 全綠
- [ ] CLAUDE.md（常用指令 + Twitter Adapter 說明：anonymous/login、不支援 --with-comments）+ `horus-scraping` skill 同步更新

## 7. 參考

- 範本 adapter: `src/horus/adapters/threads.py`（response 攔截 + positional 串鏈最接近）
- 串鏈/前端複用: `src/horus/core/thread_tree.py`（US-002/US-003）
- 偵察腳本: `/tmp/x_recon.py`、`/tmp/x_dig.py`（可重跑驗證 schema）
- 真實 fixture: `tests/fixtures/twitter/user_tweets.json`（~570KB）
