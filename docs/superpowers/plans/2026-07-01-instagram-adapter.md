# Instagram Adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `instagram` site adapter，爬取 IG profile 貼文（`--user`）與單一貼文（`--url`），並用 `--with-comments` 抓頂層留言，全程走 GraphQL/XHR 攔截。

**Architecture:** 鏡像 `threads`/`twitter` 的 `scrape()` 攔截模式。`parse_response` 三向 dispatch（timeline graphql 貼文 / `media/info` 單貼 / `media/comments` 留言）。留言因 body 內含 `media_id` 可直接綁回貼文，**不新增 core 介面**：只加一個 `comments_via_interception` ClassVar 旗標 + cli.py 明確分支。共用的 Meta 媒體解析抽成 `adapters/_meta.py`。

> **留言階段隔離（codex review 修正，關鍵設計）**：IG 的**單一** `get_response_filter` 會同時命中 timeline / media_info / comments 三種 response（維持 base API 簽章不變，不引入 `mode` 參數）。但導航貼文頁時 comments XHR 會**自動**觸發，使得主爬階段的 `parse_response` 也吐出留言（`url=""` 未回填）。因此**主爬的 `on_batch` 對 `comments_via_interception` adapter 一律丟棄 `is_reply` 項目**（不寫 DB、不 `_emit`），留言**只**經由 `--with-comments` 迴圈回填 URL 後才持久化。此設計同時涵蓋「沒帶 `--with-comments` 時留言不該落地」的情況。
> **留言歸屬校驗**：recon 實測 comment `media_id`（如 `3921942437490559575`，裸 pk 無 `_ownerid` 後綴）**等於**貼文 `pk`；故 backfill 迴圈以 `comment.extra["parent_post_id"] == post.id` 過濾，杜絕跨貼文誤掛。

**Tech Stack:** Python 3.13, Playwright（攔截）, pydantic, Click, pytest（純 parser 測試用消毒後真實 fixtures，CI 無 network）。

**Spec:** `docs/superpowers/specs/2026-06-30-instagram-adapter-design.md`（含 recon ground truth）。

---

## File Structure

| 檔案 | 職責 | 動作 |
|---|---|---|
| `tests/fixtures/instagram/{user_timeline,media_info,media_comments}.json` | 消毒後真實 response | Create |
| `src/horus/adapters/_meta.py` | 共用 Meta 解析（`_extract_media_urls`, `_MEDIA_TYPE_MAP`） | Create |
| `src/horus/adapters/threads.py` | 改 import 共用 helper（行為不變） | Modify |
| `src/horus/adapters/instagram.py` | InstagramAdapter | Create |
| `src/horus/adapters/__init__.py` | import + register | Modify |
| `src/horus/adapters/base.py` | 新增 `comments_via_interception` ClassVar | Modify |
| `src/horus/cli.py` | `--with-comments` 攔截分支 + url 回填（`:301-325`） | Modify |
| `tests/adapters/test_instagram_adapter.py` | parser/filter 測試 | Create |
| `tests/adapters/test_instagram_cli.py` | cli 分支測試 | Create |
| `CLAUDE.md` / horus-scraping skill / backlog | 文件同步 | Modify |

---

## Chunk 1: Fixtures + 共用 helper + 解析

### Task 1: 製作消毒後 fixtures

**Files:** Create `tests/fixtures/instagram/user_timeline.json`, `media_info.json`, `media_comments.json`

來源在 gitignored `scripts/recon_ig_out/`：`resp_005.json`(timeline)、`resp_007.json`(media info)、`resp_008.json`(comments)。每個 recon 檔是 `{url,status,data_keys,body}` 包了一層。

- [ ] **Step 1: 取出 `.body` 並消毒**

對三個檔，取 `.body` 當 fixture 根；然後消毒（**強制**）。

> ⚠️ **codex review 修正（finding 5）**：消毒**不可只洗留言 user**。timeline / media_info 的 node 內含大量巢狀真人資料（`user`、`owner`、`coauthor_producers[]`、`usertags`、`caption.text`、各種 `profile_pic_url`、`*_tracking_token`），**全部**要遞迴清乾淨。

**遞迴消毒規則（對三檔一致套用，用一次性 python 腳本遞迴走訪 dict/list）：**
- **刪除**任何 key 名含 `tracking_token`、`viewer`、`xdt_viewer`、`fbid`、`profile_pic`、`hd_profile_pic`、`friendship_status`、`account_badges` 的節點。
- 任何 `user`/`owner`/`coauthor_producers[]` 物件：只保留 `pk`（改假值，如 `"1001"`/`"1002"`）與 `username`（改 `test_user_N`）、`full_name`（改 `Test User N`）；其餘欄位刪除。
- 清空 `usertags`（設為 `{"in": []}` 或刪除）。
- 任何 `caption.text` / 貼文文字 / `text`：換成中性字串（如 `"貼文測試一"` / `"留言測試一"`）。
- 媒體 URL（`image_versions2` / `video_versions` / `carousel_media[].*`）：可保留結構但把實際 CDN URL 換成 `https://example.com/media/N.jpg` 之類假值（**保留 parser 需要的欄位形狀**：`candidates[].url`、`video_versions[].url`、`carousel_media[]`）。
- **縮小**：timeline / media_info 保留 1–2 個貼文 node（**含一個 carousel `media_type=8` 與一個單圖/影片**）；comments 只留 2–3 則。
- **保留 parser 依賴的欄位**：貼文 `pk`/`code`/`taken_at`/`media_type`/`like_count`/`comment_count`；留言 `pk`/`media_id`/`created_at`/`comment_like_count`/`child_comment_count`。
- 確認 fixture 根層有 dispatch 需要的鍵：timeline→`data.xdt_api__v1__feed__user_timeline_graphql_connection`；media_info→`items`；comments→`comments`+`comment_count`。

- [ ] **Step 1b: 消毒後自動掃描殘留 PII（gate）**

Run（一次性腳本即可）：遞迴 grep fixture JSON，確認**不存在** `tracking_token` / `profile_pic` / `fbid` / `viewer` 等 key，且沒有殘留真實 username（比對 recon 原檔的真實帳號名不得出現在 fixture）。任一命中 → 回 Step 1 補洗。

- [ ] **Step 2: 驗證 fixtures 可被 json 載入且結構正確**

Run: `uv run python -c "import json; [print(f, list(json.load(open('tests/fixtures/instagram/'+f)).keys())[:3]) for f in ['user_timeline.json','media_info.json','media_comments.json']]"`
Expected: 三檔都能載入；timeline 有 `data`、media_info 有 `items`、comments 有 `comments`。

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/instagram/
git commit -m "test: add sanitized instagram response fixtures (timeline/media_info/comments)"
```

> ⚠️ commit 前再次確認**無真實第三方帳號/留言內容**、無 viewer/token 欄位。

---

### Task 2: 抽共用 Meta 解析到 `_meta.py`（DRY，threads 行為不變）

**Files:** Create `src/horus/adapters/_meta.py`；Modify `src/horus/adapters/threads.py`

- [ ] **Step 1: 建立 `_meta.py`**

把 threads.py 的 `_MEDIA_TYPE_MAP`(threads.py:14-19) 與 `_extract_media_urls`(threads.py:22-48) **原封搬到** `src/horus/adapters/_meta.py`（內容不變）。

- [ ] **Step 2: threads.py 改 import**

threads.py 刪除這兩段本地定義，改 `from horus.adapters._meta import _MEDIA_TYPE_MAP, _extract_media_urls`。

- [ ] **Step 3: 確認全測試與 lint 全綠（無回歸）**

Run: `uv run pytest tests/ -q && uv run ruff check src/ && uv run ty check src/`
Expected: PASS（純移動，行為不變）。

> codex review 修正（finding 8）：`adapters/__init__.py` 會 import 所有 adapter，搬移共用 helper 可能牽動非 threads 路徑，故跑**全量** `pytest tests/`，不只 `-k threads`。

- [ ] **Step 4: Commit**

```bash
git add src/horus/adapters/_meta.py src/horus/adapters/threads.py
git commit -m "refactor: extract shared Meta media helpers to adapters/_meta.py"
```

---

### Task 3: instagram.py — 貼文解析（timeline）TDD

**Files:** Create `src/horus/adapters/instagram.py`；Create `tests/adapters/test_instagram_adapter.py`

- [ ] **Step 1: 寫失敗測試（timeline 貼文解析）**

```python
# tests/adapters/test_instagram_adapter.py
import json
from pathlib import Path
from horus.adapters.instagram import InstagramAdapter

FIX = Path(__file__).parent.parent / "fixtures" / "instagram"

def _load(name): return json.loads((FIX / name).read_text())

def test_parse_timeline_posts():
    items = InstagramAdapter().parse_response(_load("user_timeline.json"))
    assert items, "should parse at least one post"
    p = items[0]
    assert p.site_id == "instagram"
    assert p.url.startswith("https://www.instagram.com/p/")
    assert p.author_name  # username present
    assert p.timestamp is not None
    assert p.extra["is_reply"] is False
    assert "like_count" in p.extra and "comment_count" in p.extra
    assert "media_urls" in p.extra
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_instagram_adapter.py::test_parse_timeline_posts -v`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 建立 instagram.py（含 `_parse_item` + timeline dispatch）**

```python
# src/horus/adapters/instagram.py
"""Instagram adapter — GraphQL/XHR interception (mirrors threads/twitter).

Posts: graphql timeline connection OR /api/v1/media/<id>/info/.
Comments (top-level): /api/v1/media/<id>/comments/ — top-level only.
Credentials: requires manual login (horus login instagram); no anonymous access.
"""
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from horus.adapters._meta import _MEDIA_TYPE_MAP, _extract_media_urls
from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

_TIMELINE_KEY = "xdt_api__v1__feed__user_timeline_graphql_connection"


def _parse_item(node: dict[str, Any]) -> ScrapedItem | None:
    """Parse one IG media node (timeline node OR media/info item) into a ScrapedItem."""
    taken_at = node.get("taken_at")
    if taken_at is None:
        return None
    code = node.get("code", "")
    user = node.get("user", {}) or {}
    caption = node.get("caption") or {}
    media_type_code = node.get("media_type")
    media_type = _MEDIA_TYPE_MAP.get(media_type_code) if isinstance(media_type_code, int) else None
    return ScrapedItem(
        id=str(node.get("pk", "")),
        site_id="instagram",
        url=f"https://www.instagram.com/p/{code}/",
        text=caption.get("text"),
        author_id=str(user.get("pk", "")) or None,
        author_name=user.get("username") or None,
        timestamp=datetime.fromtimestamp(taken_at, tz=UTC),
        extra={
            "like_count": node.get("like_count", 0) or 0,
            "comment_count": node.get("comment_count", 0) or 0,
            "media_type": media_type,
            "media_urls": _extract_media_urls(node),
            "is_reply": False,
            "parent_post_id": None,
            "conversation_id": None,
        },
    )


class InstagramAdapter(SiteAdapter):
    site_id = "instagram"
    display_name = "Instagram (instagram.com)"
    login_url = "https://www.instagram.com/accounts/login/"
    requires_login = True
    description = "Scrape Instagram posts + top-level comments via GraphQL/XHR interception"
    comments_via_interception = True

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        data = body.get("data")
        if isinstance(data, dict) and _TIMELINE_KEY in data:
            edges = (data[_TIMELINE_KEY] or {}).get("edges", []) or []
            return [it for e in edges if (it := _parse_item(e.get("node", {}) or {}))]
        # media/info and comments dispatch added in later tasks
        return []

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        # full 3-way filter added in Task 6
        def _f(url: str, body: dict[str, Any]) -> bool:
            return "graphql" in url and _TIMELINE_KEY in str(body.get("data", {}).keys())
        return _f

    def get_urls(self, **kwargs: Any) -> list[str]:
        raise NotImplementedError  # Task 6
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/adapters/test_instagram_adapter.py::test_parse_timeline_posts -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/horus/adapters/instagram.py tests/adapters/test_instagram_adapter.py
git commit -m "feat: instagram timeline post parsing"
```

---

### Task 4: media/info dispatch（`--url` 單貼）TDD

**Files:** Modify `instagram.py`, `tests/adapters/test_instagram_adapter.py`

- [ ] **Step 1: 寫失敗測試**

```python
def test_parse_media_info_single_post():
    items = InstagramAdapter().parse_response(_load("media_info.json"))
    assert len(items) >= 1
    assert items[0].site_id == "instagram"
    assert items[0].url.startswith("https://www.instagram.com/p/")
    assert items[0].extra["is_reply"] is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_instagram_adapter.py::test_parse_media_info_single_post -v`
Expected: FAIL（回 `[]`）

- [ ] **Step 3: 在 `parse_response` 加 media/info 分支**

在 timeline 分支後、`return []` 前加：
```python
        items = body.get("items")
        if isinstance(items, list) and items and "comments" not in body:
            return [it for n in items if (it := _parse_item(n or {}))]
```

- [ ] **Step 4: 跑測試確認通過**；**Step 5: Commit** `feat: instagram single-post (media/info) parsing`

---

### Task 5: 留言解析（comments dispatch）TDD

**Files:** Modify `instagram.py`, `tests/adapters/test_instagram_adapter.py`

- [ ] **Step 1: 寫失敗測試**

```python
def test_parse_comments():
    items = InstagramAdapter().parse_response(_load("media_comments.json"))
    assert items, "should parse comments"
    c = items[0]
    assert c.site_id == "instagram"
    assert c.extra["is_reply"] is True
    # media_id binds comment back to its post
    assert c.extra["conversation_id"] == c.extra["parent_post_id"]
    assert c.extra["parent_post_id"]  # non-empty (the media_id)
    assert c.url == ""  # url backfilled later in cli.py from post.url
    assert "comment_like_count" in c.extra
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_instagram_adapter.py::test_parse_comments -v`
Expected: FAIL

- [ ] **Step 3: 加 `_parse_comment` + comments 分支**

模組層加：
```python
def _parse_comment(c: dict[str, Any]) -> ScrapedItem | None:
    created = c.get("created_at")
    if created is None:
        return None
    media_id = str(c.get("media_id", ""))
    user = c.get("user", {}) or {}
    return ScrapedItem(
        id=str(c.get("pk", "")),
        site_id="instagram",
        url="",  # backfilled in cli.py from the parent post.url (comment body lacks shortcode)
        text=c.get("text"),
        author_id=str(user.get("pk", "")) or None,
        author_name=user.get("username") or None,
        timestamp=datetime.fromtimestamp(created, tz=UTC),
        extra={
            "comment_like_count": c.get("comment_like_count", 0) or 0,
            "child_comment_count": c.get("child_comment_count", 0) or 0,
            "is_reply": True,
            "parent_post_id": media_id,
            "conversation_id": media_id,
            "media_type": None,
            "media_urls": [],
        },
    )
```
在 `parse_response` media/info 分支後加（放在 media/info 分支**之前**亦可，但要先判 comments）：
```python
        comments = body.get("comments")
        if isinstance(comments, list):
            return [it for c in comments if (it := _parse_comment(c or {}))]
```
> 注意 dispatch 順序：comments body 也可能含 `caption`，但**沒有頂層 `items`**；timeline 有 `data`。三者頂層鍵互斥（`data` / `items` / `comments`），順序：先 `data`(timeline) → 再 `comments` → 再 `items`(media_info)。請依此排列避免誤判。

- [ ] **Step 4: 加負向 / shape-drift 測試（codex review finding 3）**

Happy path fixtures 之外，補下列不需新 fixture 的邊界測試（就地建構 dict 或從 fixture 淺拷貝後刪欄位）：
```python
def test_parse_empty_and_malformed():
    a = InstagramAdapter()
    # 空 / 缺頂層鍵 → 回空 list，不 raise
    assert a.parse_response({}) == []
    assert a.parse_response({"data": {}}) == []
    assert a.parse_response({"items": []}) == []
    assert a.parse_response({"comments": []}) == []
    # 缺 taken_at 的貼文 node → 被 _parse_item 跳過（回 None 濾除）
    assert a.parse_response({"items": [{"pk": "1", "code": "X"}]}) == []
    # 缺 media_id 的留言 → parent_post_id/conversation_id 為空字串（可被 cli 過濾掉，不誤掛）
    out = a.parse_response({"comments": [{"pk": "9", "created_at": 1700000000}]})
    assert out and out[0].extra["parent_post_id"] == ""
    # 缺 created_at 的留言 → 跳過
    assert a.parse_response({"comments": [{"pk": "9"}]}) == []
    # 缺 code 的貼文 → url 仍為合法字串（不 raise），caller 可判斷
    out = a.parse_response({"items": [{"pk": "1", "taken_at": 1700000000}]})
    assert out and out[0].url == "https://www.instagram.com/p//"
```
> 這些也回頭保護 `_parse_item`/`_parse_comment` 的 `None` 跳過語意，並確保「缺 `media_id` 的留言不會被 backfill 誤掛到某貼文」（Task 8 的 `parent_post_id == post.id` 過濾會擋掉空字串）。

- [ ] **Step 5: 跑測試確認通過**；**Step 6: Commit** `feat: instagram top-level comment parsing (media_id linkage) + edge-case tests`

---

### Task 6: filter（3-way）+ get_urls + options + 註冊 TDD

**Files:** Modify `instagram.py`, `__init__.py`, `tests/adapters/test_instagram_adapter.py`

- [ ] **Step 1: 寫失敗測試**

```python
def test_filter_matches_three_endpoints():
    f = InstagramAdapter().get_response_filter()
    assert f("https://www.instagram.com/graphql/query", _load("user_timeline.json"))
    assert f("https://www.instagram.com/api/v1/media/123/info/", _load("media_info.json"))
    assert f("https://www.instagram.com/api/v1/media/123/comments/?x=1", _load("media_comments.json"))
    assert not f("https://www.instagram.com/api/v1/other/", {"foo": 1})

def test_get_urls_user_and_url():
    a = InstagramAdapter()
    assert a.get_urls(user="@nasa") == ["https://www.instagram.com/nasa/"]
    assert a.get_urls(url="https://www.instagram.com/p/ABC/") == ["https://www.instagram.com/p/ABC/"]

def test_registered():
    from horus.adapters import get_adapter
    assert get_adapter("instagram").site_id == "instagram"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/adapters/test_instagram_adapter.py -k "filter or get_urls or registered" -v`
Expected: FAIL

- [ ] **Step 3: 實作完整 filter + get_urls + options，並註冊**

`get_response_filter` 改為雙重過濾（URL + body）三向：
```python
    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        def _f(url: str, body: dict[str, Any]) -> bool:
            data = body.get("data")
            if "graphql" in url and isinstance(data, dict) and _TIMELINE_KEY in data:
                return True
            if "/api/v1/media/" in url:
                if "/comments/" in url and isinstance(body.get("comments"), list):
                    return True
                if "/info/" in url and isinstance(body.get("items"), list):
                    return True
            return False
        return _f
```
`get_urls`（同 threads 形狀，支援 stdin 多 user）：
```python
    def get_urls(self, **kwargs: Any) -> list[str]:
        url = kwargs.get("url")
        if url:
            return [url]
        user = kwargs.get("user")
        if user:
            return [f"https://www.instagram.com/{user.lstrip('@')}/"]
        import sys
        if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
            try:
                users = [ln.strip().lstrip("@") for ln in sys.stdin if ln.strip()]
            except OSError:
                users = []
            if users:
                return [f"https://www.instagram.com/{u}/" for u in users]
        raise ValueError("instagram adapter requires --user or --url")

    def get_crawl_options(self) -> list[dict[str, Any]]:
        # codex review finding 7: 單貼 --url 也在 scope 內，metadata 要一併宣告
        return [
            {"name": "--user", "help": "Instagram username (e.g. @nasa)",
             "required": False, "default": None},
            {"name": "--url", "help": "Single Instagram post URL",
             "required": False, "default": None},
        ]
```
測試補一條 `test_crawl_options_expose_user_and_url`：`{o["name"] for o in InstagramAdapter().get_crawl_options()} == {"--user", "--url"}`。
`__init__.py`：`from horus.adapters.instagram import InstagramAdapter` + `register(InstagramAdapter)`。

- [ ] **Step 4: 跑測試 + list-sites**

Run: `uv run pytest tests/adapters/test_instagram_adapter.py -v && uv run horus list-sites`
Expected: 全 PASS；list-sites 含 `instagram`。

- [ ] **Step 5: Commit** `feat: instagram filter, get_urls, registration`

---

## Chunk 2: Core 旗標 + CLI 攔截留言分支

### Task 7: base.py 新增 `comments_via_interception` ClassVar

**Files:** Modify `src/horus/adapters/base.py`

- [ ] **Step 1: 加旗標**

在 `SiteAdapter` 的 ClassVar 區（`has_http_mode` 附近）加：
```python
    comments_via_interception: ClassVar[bool] = False  # True = --with-comments 走 scrape() 攔截而非 HTML
```

- [ ] **Step 2: 確認既有測試全綠（threads 等預設 False，不受影響）**

Run: `uv run pytest tests/ -q && uv run ty check src/`
Expected: PASS

- [ ] **Step 3: Commit** `feat: add comments_via_interception flag to SiteAdapter`

---

### Task 8: cli.py — 攔截式留言分支 + url 回填 TDD

**Files:** Modify `src/horus/cli.py:301-325`；Create `tests/adapters/test_instagram_cli.py`

- [ ] **Step 1: 寫失敗測試（CliRunner + mock scraper）**

```python
# tests/adapters/test_instagram_cli.py
# 用 unittest.mock 패치 BaseScraper.scrape / scrape_comments（對齊 test_serper_cli.py 的
# CliRunner + AsyncMock 慣例），mock storage.upsert_items 收集實際落地的 items。
```
需涵蓋下列案例（斷言明確）：
1. **`test_ig_with_comments_uses_interception`**：主爬回一篇 post(is_reply False)，comment scrape 回一則 is_reply True url=""。斷言：`scrape_comments` **未**被呼叫；落地留言 `url == post.url`。
2. **`test_ig_mixed_page_defers_comments`（codex finding 1+4，關鍵）**：主爬 `on_batch` 收到的**同一批**含 `[post(is_reply False), comment(is_reply True, url="")]`。斷言：主爬階段**不**持久化 / `_emit` 該 comment（DB 落地此階段只有 post）；comment 只在後續 `--with-comments` 迴圈回填 `url` 後才落地。
3. **`test_ig_backfill_rejects_foreign_comment`（codex finding 2）**：comment scrape 回兩則，一則 `parent_post_id == post.id`、一則 `parent_post_id == "other"`。斷言：只有前者被落地且 `url == post.url`；後者被丟棄（不誤掛）。
4. **`test_ig_no_with_comments_drops_comments`（codex finding 1）**：`--url <post>` **不帶** `--with-comments`，主爬 batch 含 comment。斷言：留言完全不落地、不 `_emit`（comment loop 未跑）。

- [ ] **Step 2: 跑測試確認失敗**

改動有**兩處**：(A) 主爬 `on_batch` 加守衛丟棄攔截式留言；(B) 留言區改二分支 + 歸屬過濾。

**(A) `on_batch`（cli.py:274-286）加守衛（codex finding 1）**

在 `on_batch` 內、`adapter.post_process(batch)` **之前**插入：
```python
                    def on_batch(batch: list[ScrapedItem]) -> None:
                        nonlocal total_found, total_new
                        # comments_via_interception：導航貼文頁會自動觸發 comments XHR，
                        # 留言(url="")會混進主爬 batch。此處一律丟棄 is_reply，改由
                        # --with-comments 迴圈回填 URL 後才落地（沒帶 --with-comments 時亦不落地）。
                        if getattr(adapter_cls, "comments_via_interception", False):
                            batch = [p for p in batch if not p.extra.get("is_reply", False)]
                        if not batch:
                            return
                        batch = adapter.post_process(batch)
                        new_count = storage.upsert_items(batch)
                        total_found += len(batch)
                        total_new += new_count
                        for item in batch:
                            _emit(item)
                        if with_comments:
                            crawled_top_level.extend(
                                p for p in batch if not p.extra.get("is_reply", False)
                            )
```
> `adapter_cls` 為當前 adapter 類別（cli 既有變數；若名稱不同，取 `type(adapter)`）。守衛**不以 `with_comments` 為條件** —— 沒帶 `--with-comments` 也要擋掉留言落地。

**(B) 留言區（cli.py:301-325）改二分支 + 歸屬過濾（codex finding 2）**

```python
                    if with_comments and crawled_top_level:
                        console.print(f"Fetching comments for {len(crawled_top_level)} posts...")
                        use_intercept = getattr(adapter_cls, "comments_via_interception", False)
                        comment_parser = None if use_intercept else adapter.get_comment_parser()
                        for post in crawled_top_level:
                            try:
                                if use_intercept:
                                    raw = await scraper.scrape(
                                        post.url, adapter.get_response_filter(),
                                        adapter.parse_response, state_path,
                                    )  # no since= : comments aren't filtered by post cutoff
                                    comment_items = []
                                    for c in raw:
                                        if not c.extra.get("is_reply"):
                                            continue
                                        # 歸屬校驗：只收 media_id == 本貼 pk 的留言，杜絕跨貼文誤掛
                                        if c.extra.get("parent_post_id") != post.id:
                                            continue
                                        c.url = post.url  # backfill (comment body lacks shortcode)
                                        comment_items.append(c)
                                elif comment_parser:
                                    comment_items = await scraper.scrape_comments(
                                        url=post.url, post_pk=post.id,
                                        parser=comment_parser, state_path=state_path)
                                else:
                                    comment_items = []
                                if comment_items:
                                    new_count = storage.upsert_items(comment_items)
                                    total_found += len(comment_items)
                                    total_new += new_count
                                    console.print(f"  [dim]{post.url}: {len(comment_items)} "
                                                  f"comments ({new_count} new)[/dim]")
                            except Exception as e:
                                console.print(f"  [yellow]Warning: failed to fetch comments "
                                              f"for {post.url}: {e}[/yellow]")
```
（threads 走 `elif comment_parser` 分支，行為與原本一致。）

- [ ] **Step 4: 跑測試 + threads 回歸**

Run: `uv run pytest tests/ -k "instagram_cli or threads or comments" -v`
Expected: PASS（threads HTML 留言路徑不變）

- [ ] **Step 5: Commit** `feat: cli --with-comments interception branch + comment url backfill`

---

## Chunk 3: Step 0 執行驗證 + 文件

### Task 9: Step 0 執行驗證（手動 gate，登入後實跑）

**這是硬性 gate，沒過不算完成。** 純 parser 測試（Chunk 1）不需網路，但「導航貼文頁是否自動觸發 comments XHR」必須實機驗證。

- [ ] **Step 1: 登入並實跑單貼 + 留言**

```bash
uv run horus login instagram          # 手動登入存 state（建議分身帳號）
uv run horus crawl instagram --url "https://www.instagram.com/p/<某公開貼文>/" --with-comments
uv run horus show --site instagram --limit 20
```
Expected：
- 貼文被存（site=instagram）。
- **頂層留言被存**（`is_reply=true`、`conversation_id`/`parent_post_id` = 貼文 pk、`url` = 該貼 URL）。

- [ ] **Step 2: 若首批留言沒被攔到 → 修觸發方式**

若 `scrape()` 的 `goto` 沒觸發 comments XHR：在導航後加「點開/滾動留言區」動作再攔（spec §2 已預告此風險）。實作於 instagram 專屬流程或 scraper 既有 scroll；**最小改動**達成首批留言可攔即可。記錄實際行為。

- [ ] **Step 3: 也驗 profile 模式**

```bash
uv run horus crawl instagram --user @<某公開帳號> --with-comments
```
Expected：多篇貼文 + 各自頂層留言。

- [ ] **Step 4: Commit（若 Step 2 有改動）** `fix: ensure first-batch comments captured on post navigation`

> 不寫自動化測試（需登入 + 真網路）；以本手動 gate 為準，結果回報給使用者。

---

### Task 10: 文件 + backlog

**Files:** Modify `CLAUDE.md`、`~/.claude/skills/horus-scraping/SKILL.md`（外部，不 commit）、Obsidian backlog

- [ ] **Step 1: CLAUDE.md**
  - 「常用指令」加 `horus login instagram` / `horus crawl instagram --user @name [--with-comments]` / `--url`。
  - 「專案結構」adapters 加 `instagram.py`、`_meta.py`。
  - 新增「Instagram Adapter 說明」段：攔截三 endpoint、`requires_login`、只頂層留言、媒體只存 URL、`comments_via_interception` 機制（主爬 `on_batch` 丟棄攔截式留言、留言經 `--with-comments` 迴圈以 `parent_post_id == post.id` 歸屬後回填 URL 落地）、已知限制：
    - 留言只保證**首批**（分頁 cursor 未做，記 backlog）。
    - **carousel 影片 URL 未抓**（codex finding 9）：`_extract_media_urls` 對 carousel 只取圖片 `candidates`，carousel 內的 `video_versions` 未展開（MVP 決策，非 recon 不符）。單則影片貼文的 `video_versions` 有抓。
    - 帳號封號風險（建議分身帳號）。

- [ ] **Step 2: horus-scraping skill** 加 instagram 段（與 threads 並列：登入、`--user`/`--url`/`--with-comments`、頂層留言、限制）。CLAUDE.md 規範要求 adapter 變更必更新此 skill。

- [ ] **Step 3: 全量驗證**

Run: `uv run pytest tests/ -v && uv run ruff check src/ tests/ && uv run ty check src/ && uv run horus list-sites`
Expected: 全綠、含 instagram。

- [ ] **Step 4: backlog**（Obsidian）新增 US（Instagram adapter）標 done、更新 Kanban/Progress；若 Step 0 留言分頁未做，另記 backlog（cursor 分頁）。

- [ ] **Step 5: Commit** `chore: document instagram adapter + update backlog`

---

## 完成標準

- [ ] `horus list-sites` 顯示 `instagram`
- [ ] `--user @name` 取得貼文存 DB（增量），`--url <post>` 單貼可用
- [ ] `--with-comments` 抓到頂層留言，`is_reply`/`conversation_id`/`parent_post_id`/`url` 正確
- [ ] **主爬階段不落地攔截式留言**（`url=""` 不進 DB / 不 `_emit`）；沒帶 `--with-comments` 時留言完全不落地（codex finding 1）
- [ ] backfill 以 `parent_post_id == post.id` 歸屬，跨貼文留言不誤掛（codex finding 2）
- [ ] **Step 0 手動 gate 通過**：實機確認首批留言可攔
- [ ] threads HTML 留言路徑不受影響（回歸綠）
- [ ] parser 測試用消毒後真實 fixtures（**遞迴清除巢狀第三方 PII**、無 token），並含負向/shape-drift 案例，CI 無 network
- [ ] `_meta.py` 抽離後 threads 行為不變
- [ ] CLAUDE.md + horus-scraping skill 同步更新
- [ ] `uv run pytest` / `ruff check` / `ty check` 全綠
