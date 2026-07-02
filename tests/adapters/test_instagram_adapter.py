import json
from pathlib import Path

from horus.adapters.instagram import InstagramAdapter, post_code_from_url

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


def test_comment_media_id_matches_post_pk() -> None:
    # Load-bearing invariant: cli.py backfill attributes comments to a post via
    # `comment.extra["parent_post_id"] == post.id`. This locks that the fixtures'
    # comment media_id equals the media_info post pk, guarding against parser/fixture drift.
    a = InstagramAdapter()
    post = a.parse_response(_load("media_info.json"))[0]
    comments = a.parse_response(_load("media_comments.json"))
    assert comments
    for c in comments:
        assert c.extra["parent_post_id"] == post.id


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


# ---------------------------------------------------------------------------
# Task 6: filter (3-way) + get_urls + options + registration
# ---------------------------------------------------------------------------


def test_filter_matches_three_endpoints() -> None:
    f = InstagramAdapter().get_response_filter()
    assert f("https://www.instagram.com/graphql/query", _load("user_timeline.json"))
    assert f("https://www.instagram.com/api/v1/media/123/info/", _load("media_info.json"))
    assert f(
        "https://www.instagram.com/api/v1/media/123/comments/?x=1",
        _load("media_comments.json"),
    )
    assert not f("https://www.instagram.com/api/v1/other/", {"foo": 1})


def test_get_urls_user_and_url() -> None:
    a = InstagramAdapter()
    assert a.get_urls(user="@nasa") == ["https://www.instagram.com/nasa/"]
    assert a.get_urls(url="https://www.instagram.com/p/ABC/") == [
        "https://www.instagram.com/p/ABC/"
    ]


def test_crawl_options_expose_user_and_url() -> None:
    assert {o["name"] for o in InstagramAdapter().get_crawl_options()} == {"--user", "--url"}


def test_registered() -> None:
    from horus.adapters import get_adapter

    assert get_adapter("instagram").site_id == "instagram"


def test_comments_via_interception_flag() -> None:
    # load-bearing: cli.py routes --with-comments through interception based on this
    assert InstagramAdapter.comments_via_interception is True


# ---------------------------------------------------------------------------
# --url single-post support (SSR-inlined post)
# ---------------------------------------------------------------------------


def test_is_post_url() -> None:
    a = InstagramAdapter()
    assert a.is_post_url("https://www.instagram.com/p/DZtid4YJ4pX/")
    assert a.is_post_url("https://www.instagram.com/reel/ABC123/")
    assert not a.is_post_url("https://www.instagram.com/nasa/")
    assert not a.is_post_url("https://www.instagram.com/")


def test_post_code_from_url() -> None:
    assert post_code_from_url("https://www.instagram.com/p/DZtid4YJ4pX/") == "DZtid4YJ4pX"
    assert post_code_from_url("https://www.instagram.com/reel/ABC123/?x=1") == "ABC123"
    assert post_code_from_url("https://www.instagram.com/nasa/") is None


def test_extract_inline_items_from_ssr() -> None:
    # A post page inlines the post as xdt_api__v1__media__shortcode__web_info ({items:[...]}),
    # embedded in a <script type="application/json"> tag. Reuse the media_info fixture as the node.
    a = InstagramAdapter()
    web_info = _load("media_info.json")  # {"items": [...]}
    blob = {"require": [], "xdt_api__v1__media__shortcode__web_info": web_info}
    html = (
        '<html><body><script type="application/json">'
        + json.dumps(blob)
        + "</script></body></html>"
    )
    extracted = a.extract_inline_items(html)
    assert "items" in extracted
    # parse_response's items branch turns it into the post
    posts = a.parse_response(extracted)
    assert posts and posts[0].site_id == "instagram"
    assert posts[0].url.startswith("https://www.instagram.com/p/")
    assert posts[0].extra["is_reply"] is False


def test_extract_inline_items_absent() -> None:
    a = InstagramAdapter()
    assert a.extract_inline_items("<html><body>no inlined json here</body></html>") == {}
    # present key but malformed JSON block → still {}
    bad = '<script type="application/json">xdt_api__v1__media__shortcode__web_info {oops</script>'
    assert a.extract_inline_items(bad) == {}
