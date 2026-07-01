import json
from pathlib import Path

from horus.adapters.instagram import InstagramAdapter

FIX = Path(__file__).parent.parent / "fixtures" / "instagram"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------------------
# Task 3: timeline post parsing
# ---------------------------------------------------------------------------


def test_parse_timeline_posts() -> None:
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


# ---------------------------------------------------------------------------
# Task 4: single-post (media/info) dispatch
# ---------------------------------------------------------------------------


def test_parse_media_info_single_post() -> None:
    items = InstagramAdapter().parse_response(_load("media_info.json"))
    assert len(items) >= 1
    assert items[0].site_id == "instagram"
    assert items[0].url.startswith("https://www.instagram.com/p/")
    assert items[0].extra["is_reply"] is False


# ---------------------------------------------------------------------------
# Task 5: top-level comment parsing (media_id linkage)
# ---------------------------------------------------------------------------


def test_parse_comments() -> None:
    items = InstagramAdapter().parse_response(_load("media_comments.json"))
    assert items, "should parse comments"
    c = items[0]
    assert c.site_id == "instagram"
    assert c.extra["is_reply"] is True
    assert c.extra["conversation_id"] == c.extra["parent_post_id"]
    assert c.extra["parent_post_id"]  # non-empty (the media_id)
    assert c.url == ""  # url backfilled later in cli.py from post.url
    assert "comment_like_count" in c.extra


def test_parse_empty_and_malformed() -> None:
    a = InstagramAdapter()
    assert a.parse_response({}) == []
    assert a.parse_response({"data": {}}) == []
    assert a.parse_response({"items": []}) == []
    assert a.parse_response({"comments": []}) == []
    assert (
        a.parse_response({"items": [{"pk": "1", "code": "X"}]}) == []
    )  # missing taken_at -> skipped
    out = a.parse_response(
        {"comments": [{"pk": "9", "created_at": 1700000000}]}
    )  # missing media_id
    assert out and out[0].extra["parent_post_id"] == ""
    assert a.parse_response({"comments": [{"pk": "9"}]}) == []  # missing created_at -> skipped
    out = a.parse_response({"items": [{"pk": "1", "taken_at": 1700000000}]})  # missing code
    assert out and out[0].url == "https://www.instagram.com/p//"
