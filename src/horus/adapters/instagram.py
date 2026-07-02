"""Instagram adapter — GraphQL/XHR interception (mirrors threads/twitter).

Posts: graphql timeline connection OR /api/v1/media/<id>/info/.
Comments (top-level): /api/v1/media/<id>/comments/ — top-level only.
Credentials: requires manual login (horus login instagram); no anonymous access.
"""

import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from horus.adapters._meta import _MEDIA_TYPE_MAP, _extract_media_urls
from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

_TIMELINE_KEY = "xdt_api__v1__feed__user_timeline_graphql_connection"
_WEB_INFO_KEY = "xdt_api__v1__media__shortcode__web_info"
_POST_URL_RE = re.compile(r"/(?:p|reel)/([^/?#]+)")
_JSON_SCRIPT_RE = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.DOTALL)


def post_code_from_url(url: str) -> str | None:
    """Return the shortcode from an instagram /p/<code>/ or /reel/<code>/ URL, else None."""
    m = _POST_URL_RE.search(url)
    return m.group(1) if m else None


def _find_web_info(obj: Any) -> dict[str, Any] | None:
    """Depth-first search for the shortcode web_info node ({items:[...]})."""
    if isinstance(obj, dict):
        node = obj.get(_WEB_INFO_KEY)
        if isinstance(node, dict):
            return node
        # key absent or not a dict → keep searching other branches
        for v in obj.values():
            found = _find_web_info(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_web_info(v)
            if found is not None:
                return found
    return None


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


def _parse_comment(c: dict[str, Any]) -> ScrapedItem | None:
    """Parse one IG top-level comment into a ScrapedItem."""
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


class InstagramAdapter(SiteAdapter):
    site_id = "instagram"
    display_name = "Instagram (instagram.com)"
    login_url = "https://www.instagram.com/accounts/login/"
    requires_login = True
    description = "Scrape Instagram posts + top-level comments via GraphQL/XHR interception"
    comments_via_interception = True

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        # 1. Timeline (GraphQL connection)
        data = body.get("data")
        if isinstance(data, dict) and _TIMELINE_KEY in data:
            edges = (data[_TIMELINE_KEY] or {}).get("edges", []) or []
            return [it for e in edges if (it := _parse_item(e.get("node", {}) or {}))]

        # 2. Comments (/api/v1/media/<id>/comments/)
        comments = body.get("comments")
        if isinstance(comments, list):
            return [it for c in comments if (it := _parse_comment(c or {}))]

        # 3. Single-post info (/api/v1/media/<id>/info/)
        # Comments are already handled by branch 2 (returns on any list `comments`),
        # so no cross-branch guard is needed here — a stray non-list `comments` key
        # must not suppress parsing the post.
        items = body.get("items")
        if isinstance(items, list) and items:
            return [it for n in items if (it := _parse_item(n or {}))]

        return []

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

    def is_post_url(self, url: str) -> bool:
        return _POST_URL_RE.search(url) is not None

    def extract_inline_items(self, html: str) -> dict[str, Any]:
        """Pull the inlined shortcode web_info blob from a directly-navigated post page.

        Direct navigation to /p/<code>/ inlines the post as
        `xdt_api__v1__media__shortcode__web_info` (a {items:[...]} node) instead of firing an
        interceptable XHR. Returns that node so parse_response's `items` branch handles it;
        returns {} if not found.
        """
        for block in _JSON_SCRIPT_RE.findall(html):
            if _WEB_INFO_KEY not in block:
                continue
            try:
                data = json.loads(block)
            except (ValueError, TypeError):
                continue
            node = _find_web_info(data)
            if node is not None:
                return node
        return {}

    def get_urls(self, **kwargs: Any) -> list[str]:
        url = kwargs.get("url")
        if url:
            return [url]
        user = kwargs.get("user")
        if user:
            return [f"https://www.instagram.com/{user.lstrip('@')}/"]
        if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
            try:
                users = [ln.strip().lstrip("@") for ln in sys.stdin if ln.strip()]
            except OSError:
                users = []
            if users:
                return [f"https://www.instagram.com/{u}/" for u in users]
        raise ValueError("instagram adapter requires --user or --url")

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "--user",
                "help": "Instagram username (e.g. @nasa)",
                "required": False,
                "default": None,
            },
            {
                "name": "--url",
                "help": "Single Instagram post URL",
                "required": False,
                "default": None,
            },
        ]
