# Instagram Adapter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `instagram` site adapter，爬取 IG profile 貼文（`--user`）與單一貼文（`--url`），並用 `--with-comments` 抓頂層留言，全程走 GraphQL/XHR 攔截。

**Architecture:** 鏡像 `threads`/`twitter` 的 `scrape()` 攔截模式。`parse_response` 三向 dispatch（timeline graphql 貼文 / `media/info` 單貼 / `media/comments` 留言）。留言因 body 內含 `media_id` 可直接綁回貼文，**不新增 core 介面**：只加一個 `comments_via_interception` ClassVar 旗標 + cli.py 明確分支。共用的 Meta 媒體解析抽成 `adapters/_meta.py`。

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

對三個檔，取 `.body` 當 fixture 根；然後消毒（**強制**）：
- 移除 `xdt_viewer`、任何 `*_tracking_token`、`organic_tracking_token`。
- timeline / media_info：保留 1–2 個貼文 node（含一個 carousel `media_type=8` 與一個單圖/影片），其餘 edges/items 刪除以縮小。
- comments：**只留 2–3 則**；每則的 `user.{username,full_name,pk,fbid_v2,profile_pic_url}` 換成假值（如 `test_user_1` / pk `"1001"`）、`text` 換成中性字串（如 `"留言測試一"`）。保留 `media_id`、`pk`、`created_at`、`comment_like_count`、`child_comment_count` 結構。
- 確認 fixture 根層有 dispatch 需要的鍵：timeline→`data.xdt_api__v1__feed__user_timeline_graphql_connection`；media_info→`items`；comments→`comments`+`comment_count`。

可用一次性 python 腳本（不需保留）完成。

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

- [ ] **Step 3: 確認 threads 測試與 lint 全綠（無回歸）**

Run: `uv run pytest tests/ -k "threads" -v && uv run ruff check src/ && uv run ty check src/`
Expected: PASS（純移動，行為不變）。

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

- [ ] **Step 4: 跑測試確認通過**；**Step 5: Commit** `feat: instagram top-level comment parsing (media_id linkage)`

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
        return [{"name": "--user", "help": "Instagram username (e.g. @nasa)",
                 "required": False, "default": None}]
```
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
# tests/adapters/test_instagram_cli.py — 驗證 with_comments 時 IG 走 scrape() 而非 scrape_comments，
# 且回傳的留言 url 被回填成 post.url。
# 用 unittest.mock 패치 BaseScraper.scrape / scrape_comments，
# 讓 main crawl 回一篇 post（is_reply False），comment scrape 回一則 is_reply True url=""。
# 斷言：scrape_comments 未被呼叫；upsert 的留言 url == post.url。
```
（實作測試時對齊現有 `tests/adapters/test_serper_cli.py` 的 CliRunner + AsyncMock 慣例；mock `BaseScraper.scrape` 以受控資料回傳。）

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 改 cli.py 留言區（`:301-325`）為二分支**

把現有 comment 區改為：
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
                                    comment_items = [c for c in raw if c.extra.get("is_reply")]
                                    for c in comment_items:
                                        c.url = post.url  # backfill (comment body lacks shortcode)
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
  - 新增「Instagram Adapter 說明」段：攔截三 endpoint、`requires_login`、只頂層留言、媒體只存 URL、`comments_via_interception` 機制、已知限制（留言只保證首批、帳號封號風險）。

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
- [ ] **Step 0 手動 gate 通過**：實機確認首批留言可攔
- [ ] threads HTML 留言路徑不受影響（回歸綠）
- [ ] parser 測試用消毒後真實 fixtures（無第三方 PII、無 token），CI 無 network
- [ ] `_meta.py` 抽離後 threads 行為不變
- [ ] CLAUDE.md + horus-scraping skill 同步更新
- [ ] `uv run pytest` / `ruff check` / `ty check` 全綠
