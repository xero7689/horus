from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

_MEDIA_TYPE_MAP = {
    1: "IMAGE",
    2: "VIDEO",
    8: "CAROUSEL_ALBUM",
    19: "TEXT_POST",
}


def _extract_media_urls(post_data: dict[str, Any]) -> list[str]:
    """Extract media URLs from image_versions2 or carousel_media."""
    urls: list[str] = []

    # Single image/video
    image_versions = post_data.get("image_versions2", {})
    if image_versions:
        candidates = image_versions.get("candidates", [])
        if candidates:
            urls.append(candidates[0].get("url", ""))

    # Video
    video_versions = post_data.get("video_versions", [])
    if video_versions:
        urls.append(video_versions[0].get("url", ""))

    # Carousel replaces single image
    carousel = post_data.get("carousel_media")
    if carousel:
        urls.clear()
        for item in carousel:
            img = item.get("image_versions2", {})
            candidates = img.get("candidates", [])
            if candidates:
                urls.append(candidates[0].get("url", ""))

    return [u for u in urls if u]


def _parse_item(
    post_data: dict[str, Any],
    *,
    parent_post_id: str | None = None,
    conversation_id: str | None = None,
    is_reply: bool = False,
) -> ScrapedItem | None:
    """Parse a single post from GraphQL thread_items[].post into a ScrapedItem."""
    user = post_data.get("user", {})
    username = user.get("username", "")
    user_pk = str(user.get("pk", ""))
    code = post_data.get("code", "")

    caption = post_data.get("caption") or {}
    text = caption.get("text")

    taken_at = post_data.get("taken_at")
    if taken_at is None:
        return None
    timestamp = datetime.fromtimestamp(taken_at, tz=UTC)

    media_type_code = post_data.get("media_type")
    media_type = _MEDIA_TYPE_MAP.get(media_type_code) if isinstance(media_type_code, int) else None

    media_urls = _extract_media_urls(post_data)

    text_post_info = post_data.get("text_post_app_info", {}) or {}
    reply_count = text_post_info.get("direct_reply_count", 0) or 0
    repost_count = text_post_info.get("repost_count", 0) or 0

    reply_to_author = text_post_info.get("reply_to_author") or {}
    reply_to_username = reply_to_author.get("username") if reply_to_author else None

    return ScrapedItem(
        id=str(post_data.get("pk", "")),
        site_id="threads",
        url=f"https://www.threads.net/@{username}/post/{code}",
        text=text,
        author_id=user_pk,
        author_name=username,
        timestamp=timestamp,
        extra={
            "like_count": post_data.get("like_count", 0) or 0,
            "reply_count": reply_count,
            "repost_count": repost_count,
            "media_type": media_type,
            "media_urls": media_urls,
            "is_reply": is_reply,
            "parent_post_id": parent_post_id,
            "conversation_id": conversation_id,
            "reply_to_username": reply_to_username,
        },
    )


class ThreadsAdapter(SiteAdapter):
    site_id = "threads"
    display_name = "Threads (threads.net)"
    login_url = "https://www.threads.net/login"
    requires_login = True
    description = "Scrape posts and replies from Threads users via GraphQL interception"

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        def filter_fn(url: str, body: dict[str, Any]) -> bool:
            return "graphql" in url and "mediaData" in str(body.get("data", {}).keys())

        return filter_fn

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        """Parse posts OR replies depending on response structure.

        Replies responses have thread_items with 2+ items (parent + reply).
        Posts responses have thread_items with 1 item.
        """
        media_data = body.get("data", {}).get("mediaData", {})
        edges = media_data.get("edges", [])
        if not edges:
            return []

        # Detect mode: check first edge's thread_items count
        first_node = edges[0].get("node", {})
        first_thread_items = first_node.get("thread_items", [])
        is_replies_mode = len(first_thread_items) >= 2

        if is_replies_mode:
            return self._parse_replies(edges)
        return self._parse_posts(edges)

    def _parse_posts(self, edges: list[dict[str, Any]]) -> list[ScrapedItem]:
        """Parse posts: take only thread_items[0] from each edge."""
        items: list[ScrapedItem] = []
        for edge in edges:
            node = edge.get("node", {})
            thread_items = node.get("thread_items", [])
            if not thread_items:
                continue

            post_data = thread_items[0].get("post")
            if not post_data:
                continue

            item = _parse_item(post_data)
            if item:
                items.append(item)

        return items

    def _parse_replies(self, edges: list[dict[str, Any]]) -> list[ScrapedItem]:
        """Parse replies: take all thread_items, linking parent_post_id and conversation_id."""
        items: list[ScrapedItem] = []
        for edge in edges:
            node = edge.get("node", {})
            thread_items = node.get("thread_items", [])
            if not thread_items:
                continue

            # First item's pk is the conversation root
            root_data = thread_items[0].get("post")
            if not root_data:
                continue
            conversation_id = str(root_data.get("pk", ""))

            prev_id: str | None = None
            for thread_item in thread_items:
                post_data = thread_item.get("post")
                if not post_data:
                    continue
                item = _parse_item(
                    post_data,
                    parent_post_id=prev_id,
                    conversation_id=conversation_id,
                    is_reply=(prev_id is not None),
                )
                if item:
                    items.append(item)
                    prev_id = item.id

        return items

    def get_urls(self, **kwargs: Any) -> list[str]:
        user: str | None = kwargs.get("user")
        url: str | None = kwargs.get("url")
        mode: str = kwargs.get("mode") or "posts"
        if url:
            return [url]
        if user:
            username = user.lstrip("@")
            if mode == "replies":
                return [f"https://www.threads.net/@{username}/replies"]
            return [f"https://www.threads.net/@{username}"]
        raise ValueError("threads adapter requires --user or --url")

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "--user",
                "help": "Threads username (e.g. @elonmusk)",
                "required": False,
                "default": None,
            },
            {
                "name": "--mode",
                "help": "posts or replies",
                "required": False,
                "default": "posts",
            },
        ]
