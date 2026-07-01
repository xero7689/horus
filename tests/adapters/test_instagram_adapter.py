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
