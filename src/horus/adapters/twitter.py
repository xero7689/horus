"""Twitter / X adapter — parses tweets from intercepted GraphQL responses.

Mode: response interception (has_page_mode=False, has_http_mode=False), same
path as the Threads adapter. Anonymous access works for public profiles; a
saved login state (``horus login twitter``) improves reliability under X's
guest-token rate limits.

Schema notes (see docs/superpowers/plans/2026-05-31-twitter-adapter.md):
- author lives at core.user_results.result.core (legacy.screen_name is null)
- retweet content is nested in legacy.retweeted_status_result.result
- long tweets (>280) carry the full body in note_tweet
- the pinned tweet arrives in a separate TimelinePinEntry instruction
"""

import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

_X_TIME_FORMAT = "%a %b %d %H:%M:%S %z %Y"
_TWEET_OPS = ("UserTweets", "UserTweetsAndReplies", "TweetDetail")
_VIDEO_TYPES = ("video", "animated_gif")


def _parse_x_time(value: str | None) -> datetime | None:
    """Parse X's 'Sat May 30 02:32:47 +0000 2026' into a tz-aware datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(value, _X_TIME_FORMAT)
    except (ValueError, TypeError):
        return None


def _unwrap_tweet(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve a tweet_results.result to a plain Tweet dict, or None to skip."""
    if not isinstance(result, dict):
        return None
    typename = result.get("__typename")
    if typename == "Tweet":
        return result
    if typename == "TweetWithVisibilityResults":
        inner = result.get("tweet")
        return inner if isinstance(inner, dict) else None
    return None  # tombstone / promoted / unknown — skip defensively


def _content_tweet(tweet: dict[str, Any]) -> dict[str, Any]:
    """For a retweet the real content lives in the nested retweeted tweet."""
    rt = tweet.get("legacy", {}).get("retweeted_status_result", {}).get("result")
    inner = _unwrap_tweet(rt)
    return inner if inner is not None else tweet


def _author(tweet: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (screen_name, rest_id) from the new core.user_results path."""
    result = tweet.get("core", {}).get("user_results", {}).get("result", {})
    return result.get("core", {}).get("screen_name"), result.get("rest_id")


def _text(tweet: dict[str, Any]) -> str | None:
    """Prefer note_tweet (full long-form body) over the truncated full_text."""
    note = tweet.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {}).get("text")
    return note or tweet.get("legacy", {}).get("full_text")


def _best_video_url(media: dict[str, Any]) -> str | None:
    """Pick the highest-bitrate mp4 variant, skipping the HLS (m3u8) entry."""
    variants = media.get("video_info", {}).get("variants", [])
    mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4:
        return None
    return max(mp4, key=lambda v: v.get("bitrate", 0)).get("url")


def _extract_media(legacy: dict[str, Any]) -> tuple[str | None, list[str]]:
    media = legacy.get("extended_entities", {}).get("media") or legacy.get("entities", {}).get(
        "media"
    )
    if not media:
        return None, []
    urls: list[str] = []
    for item in media:
        url = (
            _best_video_url(item)
            if item.get("type") in _VIDEO_TYPES
            else item.get("media_url_https")
        )
        if url:
            urls.append(url)
    return media[0].get("type"), urls


def _threading(
    legacy: dict[str, Any], *, prev_id: str | None, in_module: bool
) -> tuple[bool, str | None, str | None]:
    """Return (is_reply, parent_post_id, conversation_id) for a tweet.

    Conversation root (id == conversation_id) is not a reply. Inside a
    self-thread module we chain positionally (prev tweet), falling back to the
    conversation root — X often omits the actual parent from the response, so
    in_reply_to_status_id_str dangles (see plan §2.3).
    """
    conv = legacy.get("conversation_id_str")
    if legacy.get("id_str") == conv:
        return False, None, conv
    if in_module:
        return True, prev_id or conv, conv
    return True, legacy.get("in_reply_to_status_id_str"), conv


def _build_extra(
    content: dict[str, Any],
    media_type: str | None,
    media_urls: list[str],
    *,
    is_reply: bool,
    parent_id: str | None,
    conversation_id: str | None,
    is_retweet: bool,
    retweeted_by: str | None,
) -> dict[str, Any]:
    legacy = content.get("legacy", {})
    count = content.get("views", {}).get("count")
    return {
        "like_count": legacy.get("favorite_count", 0),
        "retweet_count": legacy.get("retweet_count", 0),
        "reply_count": legacy.get("reply_count", 0),
        "quote_count": legacy.get("quote_count", 0),
        "bookmark_count": legacy.get("bookmark_count", 0),
        "view_count": int(count) if isinstance(count, str) and count.isdigit() else None,
        "media_type": media_type,
        "media_urls": media_urls,
        "lang": legacy.get("lang"),
        "is_reply": is_reply,
        "is_retweet": is_retweet,
        "retweeted_by": retweeted_by,
        "is_quote": legacy.get("is_quote_status", False),
        "parent_post_id": parent_id,
        "conversation_id": conversation_id,
        "reply_to_username": legacy.get("in_reply_to_screen_name"),
    }


def _parse_tweet(
    result: dict[str, Any] | None,
    *,
    prev_id: str | None = None,
    in_module: bool = False,
) -> ScrapedItem | None:
    """Parse one tweet_results.result into a ScrapedItem, or None to skip."""
    envelope = _unwrap_tweet(result)
    if envelope is None:
        return None
    env_legacy = envelope.get("legacy", {})
    item_id = env_legacy.get("id_str")
    timestamp = _parse_x_time(env_legacy.get("created_at"))
    if not item_id or timestamp is None:
        return None

    content = _content_tweet(envelope)  # inner tweet for retweets
    screen_name, author_id = _author(content)
    media_type, media_urls = _extract_media(content.get("legacy", {}))
    is_reply, parent_id, conversation_id = _threading(
        env_legacy, prev_id=prev_id, in_module=in_module
    )
    is_retweet = "retweeted_status_result" in env_legacy
    retweeted_by = _author(envelope)[0] if is_retweet else None
    content_id = content.get("legacy", {}).get("id_str", item_id)

    return ScrapedItem(
        id=item_id,  # outer envelope id: dedup-safe, distinct timeline event
        site_id="twitter",
        url=f"https://x.com/{screen_name}/status/{content_id}",
        text=_text(content),
        author_id=author_id,
        author_name=screen_name,
        timestamp=timestamp,  # outer time: keeps `since` incremental correct
        extra=_build_extra(
            content,
            media_type,
            media_urls,
            is_reply=is_reply,
            parent_id=parent_id,
            conversation_id=conversation_id,
            is_retweet=is_retweet,
            retweeted_by=retweeted_by,
        ),
    )


def _extract_entries(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten timeline instructions into entries (UserTweets + TweetDetail roots)."""
    data = body.get("data", {})
    timeline = (
        data.get("user", {}).get("result", {}).get("timeline", {}).get("timeline")
        or data.get("threaded_conversation_with_injections_v2")
        or {}
    )
    entries: list[dict[str, Any]] = []
    for instruction in timeline.get("instructions", []):
        kind = instruction.get("type")
        if kind == "TimelineAddEntries":
            entries.extend(instruction.get("entries", []))
        elif kind == "TimelinePinEntry" and instruction.get("entry"):
            entries.append(instruction["entry"])  # pinned tweet lives here
    return entries


def _parse_module(content: dict[str, Any]) -> list[ScrapedItem]:
    """Parse a profile-conversation (self-thread) module with positional chaining."""
    items: list[ScrapedItem] = []
    prev_id: str | None = None
    for module_item in content.get("items", []):
        result = (
            module_item.get("item", {})
            .get("itemContent", {})
            .get("tweet_results", {})
            .get("result")
        )
        item = _parse_tweet(result, prev_id=prev_id, in_module=True)
        if item is not None:
            items.append(item)
            prev_id = item.id
    return items


def _parse_timeline(body: dict[str, Any]) -> list[ScrapedItem]:
    """Parse a UserTweets / UserTweetsAndReplies / TweetDetail body into items.

    Never raises: a malformed entry is skipped so one bad shape cannot abort
    the whole crawl (core.scraper does not guard parser calls).
    """
    items: list[ScrapedItem] = []
    for entry in _extract_entries(body):
        entry_id = entry.get("entryId", "")
        content = entry.get("content", {})
        try:
            if entry_id.startswith("profile-conversation-"):
                items.extend(_parse_module(content))
            elif entry_id.startswith("tweet-"):
                result = content.get("itemContent", {}).get("tweet_results", {}).get("result")
                item = _parse_tweet(result)
                if item is not None:
                    items.append(item)
            # cursor-*, who-to-follow-*, promoted-*, … → skip
        except Exception:
            continue
    return _dedup(items)


def _dedup(items: list[ScrapedItem]) -> list[ScrapedItem]:
    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            unique.append(item)
    return unique


class TwitterAdapter(SiteAdapter):
    site_id = "twitter"
    display_name = "Twitter / X (x.com)"
    login_url = "https://x.com/login"
    requires_login = False  # anonymous works for public profiles; login is steadier
    description = "Scrape posts and replies from X via GraphQL interception"

    def get_response_filter(self) -> Callable[[str, dict[str, Any]], bool]:
        def filter_fn(url: str, body: dict[str, Any]) -> bool:
            if "graphql" not in url or not any(op in url for op in _TWEET_OPS):
                return False
            data = body.get("data", {})
            user_result = data.get("user", {}).get("result", {})
            return "timeline" in user_result or "threaded_conversation_with_injections_v2" in data

        return filter_fn

    def parse_response(self, body: dict[str, Any]) -> list[ScrapedItem]:
        return _parse_timeline(body)

    def get_urls(self, **kwargs: Any) -> list[str]:
        url: str | None = kwargs.get("url")
        user: str | None = kwargs.get("user")
        mode: str = kwargs.get("mode") or "posts"
        if url:
            return [url]
        if user:
            return [self._profile_url(user, mode)]
        if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
            try:
                usernames = [line.strip().lstrip("@") for line in sys.stdin if line.strip()]
            except OSError:
                usernames = []
            if usernames:
                return [self._profile_url(u, mode) for u in usernames]
        raise ValueError("twitter adapter requires --user or --url")

    @staticmethod
    def _profile_url(user: str, mode: str) -> str:
        username = user.lstrip("@")
        if mode == "replies":
            return f"https://x.com/{username}/with_replies"
        return f"https://x.com/{username}"

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "--user",
                "help": "X username (e.g. @NASA)",
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
