import json
from datetime import UTC

from horus.adapters.threads import ThreadsAdapter

# ---------------------------------------------------------------------------
# Sample GraphQL fixture data
# ---------------------------------------------------------------------------

_SAMPLE_POST_RESPONSE: dict = {
    "data": {
        "mediaData": {
            "edges": [
                {
                    "node": {
                        "thread_items": [
                            {
                                "post": {
                                    "pk": "111111",
                                    "code": "abc123",
                                    "taken_at": 1704067200,  # 2024-01-01T00:00:00Z
                                    "user": {"pk": "u1", "username": "alice"},
                                    "caption": {"text": "Hello Threads!"},
                                    "media_type": 19,
                                    "like_count": 10,
                                    "text_post_app_info": {
                                        "direct_reply_count": 2,
                                        "repost_count": 1,
                                        "reply_to_author": None,
                                    },
                                }
                            }
                        ]
                    }
                },
                {
                    "node": {
                        "thread_items": [
                            {
                                "post": {
                                    "pk": "222222",
                                    "code": "def456",
                                    "taken_at": 1704153600,  # 2024-01-02T00:00:00Z
                                    "user": {"pk": "u1", "username": "alice"},
                                    "caption": {"text": "Second post"},
                                    "media_type": 19,
                                    "like_count": 5,
                                    "text_post_app_info": {
                                        "direct_reply_count": 0,
                                        "repost_count": 0,
                                        "reply_to_author": None,
                                    },
                                }
                            }
                        ]
                    }
                },
            ]
        }
    }
}

_SAMPLE_REPLY_RESPONSE: dict = {
    "data": {
        "mediaData": {
            "edges": [
                {
                    "node": {
                        "thread_items": [
                            {
                                "post": {
                                    "pk": "333333",
                                    "code": "root01",
                                    "taken_at": 1704067200,
                                    "user": {"pk": "u2", "username": "bob"},
                                    "caption": {"text": "Original post by bob"},
                                    "media_type": 19,
                                    "like_count": 20,
                                    "text_post_app_info": {
                                        "direct_reply_count": 1,
                                        "repost_count": 0,
                                        "reply_to_author": None,
                                    },
                                }
                            },
                            {
                                "post": {
                                    "pk": "444444",
                                    "code": "reply01",
                                    "taken_at": 1704153600,
                                    "user": {"pk": "u1", "username": "alice"},
                                    "caption": {"text": "Alice replies to bob"},
                                    "media_type": 19,
                                    "like_count": 3,
                                    "text_post_app_info": {
                                        "direct_reply_count": 0,
                                        "repost_count": 0,
                                        "reply_to_author": {"username": "bob"},
                                    },
                                }
                            },
                        ]
                    }
                }
            ]
        }
    }
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestThreadsAdapterMetadata:
    def test_site_id(self) -> None:
        assert ThreadsAdapter.site_id == "threads"

    def test_requires_login(self) -> None:
        assert ThreadsAdapter.requires_login is True

    def test_login_url(self) -> None:
        assert "threads.net" in ThreadsAdapter.login_url


class TestResponseFilter:
    def test_accepts_graphql_with_media_data(self) -> None:
        adapter = ThreadsAdapter()
        fn = adapter.get_response_filter()
        body = {"data": {"mediaData": {"edges": []}}}
        assert fn("https://www.threads.net/graphql/query", body) is True

    def test_rejects_non_graphql_url(self) -> None:
        adapter = ThreadsAdapter()
        fn = adapter.get_response_filter()
        body = {"data": {"mediaData": {}}}
        assert fn("https://www.threads.net/api/v1/something", body) is False

    def test_rejects_body_without_media_data(self) -> None:
        adapter = ThreadsAdapter()
        fn = adapter.get_response_filter()
        body = {"data": {"otherKey": {}}}
        assert fn("https://www.threads.net/graphql/query", body) is False


class TestParseResponsePosts:
    def test_parses_two_posts(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_POST_RESPONSE)
        assert len(items) == 2

    def test_post_fields(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_POST_RESPONSE)
        item = items[0]
        assert item.id == "111111"
        assert item.site_id == "threads"
        assert item.text == "Hello Threads!"
        assert item.author_name == "alice"
        assert item.timestamp.tzinfo == UTC

    def test_post_url_format(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_POST_RESPONSE)
        assert "threads.net/@alice/post/abc123" in items[0].url

    def test_post_extra_fields(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_POST_RESPONSE)
        extra = items[0].extra
        assert extra["like_count"] == 10
        assert extra["is_reply"] is False
        assert extra["parent_post_id"] is None

    def test_empty_edges(self) -> None:
        adapter = ThreadsAdapter()
        result = adapter.parse_response({"data": {"mediaData": {"edges": []}}})
        assert result == []


class TestParseResponseReplies:
    def test_parses_root_and_reply(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_REPLY_RESPONSE)
        assert len(items) == 2

    def test_root_item_not_reply(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_REPLY_RESPONSE)
        root = next(i for i in items if i.id == "333333")
        assert root.extra["is_reply"] is False
        assert root.extra["parent_post_id"] is None

    def test_reply_item_is_reply(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_REPLY_RESPONSE)
        reply = next(i for i in items if i.id == "444444")
        assert reply.extra["is_reply"] is True
        assert reply.extra["parent_post_id"] == "333333"

    def test_conversation_id_set(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_REPLY_RESPONSE)
        for item in items:
            assert item.extra["conversation_id"] == "333333"

    def test_reply_to_username(self) -> None:
        adapter = ThreadsAdapter()
        items = adapter.parse_response(_SAMPLE_REPLY_RESPONSE)
        reply = next(i for i in items if i.id == "444444")
        assert reply.extra["reply_to_username"] == "bob"


class TestGetUrls:
    def test_user_posts(self) -> None:
        adapter = ThreadsAdapter()
        urls = adapter.get_urls(user="alice")
        assert urls == ["https://www.threads.net/@alice"]

    def test_user_with_at_sign(self) -> None:
        adapter = ThreadsAdapter()
        urls = adapter.get_urls(user="@alice")
        assert urls == ["https://www.threads.net/@alice"]

    def test_user_replies_mode(self) -> None:
        adapter = ThreadsAdapter()
        urls = adapter.get_urls(user="alice", mode="replies")
        assert urls == ["https://www.threads.net/@alice/replies"]

    def test_direct_url(self) -> None:
        adapter = ThreadsAdapter()
        url = "https://www.threads.net/@alice"
        urls = adapter.get_urls(url=url)
        assert urls == [url]

    def test_no_args_raises(self) -> None:
        adapter = ThreadsAdapter()
        try:
            adapter.get_urls()
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Helpers to build minimal SSR HTML fixture
# ---------------------------------------------------------------------------

def _make_ssr_html(thread_item_arrays: list[list[dict]]) -> str:
    """Wrap thread_items arrays in minimal SSR HTML that parse_comments_from_html can parse."""
    parts = []
    for arr in thread_item_arrays:
        parts.append(f'"thread_items":{json.dumps(arr)}')
    return "<script>" + ",".join(parts) + "</script>"


def _make_post_item(
    pk: str,
    username: str,
    text: str,
    taken_at: int = 1704067200,
    reply_to_username: str | None = None,
) -> dict:
    return {
        "post": {
            "pk": pk,
            "code": pk,
            "taken_at": taken_at,
            "user": {"pk": f"u_{pk}", "username": username},
            "caption": {"text": text},
            "media_type": 19,
            "like_count": 1,
            "text_post_app_info": {
                "direct_reply_count": 0,
                "repost_count": 0,
                "reply_to_author": {"username": reply_to_username} if reply_to_username else None,
            },
        }
    }


class TestParseCommentsFromHtml:
    def test_returns_empty_when_no_thread_items(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        result = parse_comments_from_html("<html></html>", post_pk="111")
        assert result == []

    def test_skips_first_array_which_is_original_post(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        comment1 = [_make_post_item("222", "bob", "First comment")]
        html = _make_ssr_html([original_post, comment1])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 1
        assert items[0].id == "222"

    def test_root_comment_has_correct_parent_and_conversation_id(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        comment = [_make_post_item("222", "bob", "First comment", reply_to_username="alice")]
        html = _make_ssr_html([original_post, comment])
        items = parse_comments_from_html(html, post_pk="111")
        item = items[0]
        assert item.extra["is_reply"] is True
        assert item.extra["parent_post_id"] == "111"
        assert item.extra["conversation_id"] == "111"

    def test_nested_reply_links_to_root_comment(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        thread = [
            _make_post_item("222", "bob", "Root comment", reply_to_username="alice"),
            _make_post_item("333", "carol", "Reply to bob", reply_to_username="bob"),
        ]
        html = _make_ssr_html([original_post, thread])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 2
        root = next(i for i in items if i.id == "222")
        nested = next(i for i in items if i.id == "333")
        assert root.extra["parent_post_id"] == "111"
        assert nested.extra["parent_post_id"] == "222"
        assert nested.extra["conversation_id"] == "111"

    def test_multiple_comment_groups(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        group1 = [_make_post_item("222", "bob", "Comment 1", reply_to_username="alice")]
        group2 = [_make_post_item("333", "carol", "Comment 2", reply_to_username="alice")]
        html = _make_ssr_html([original_post, group1, group2])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 2
        ids = {i.id for i in items}
        assert ids == {"222", "333"}

    def test_deduplicates_by_id(self) -> None:
        from horus.adapters.threads import parse_comments_from_html
        original_post = [_make_post_item("111", "alice", "Original")]
        dup = [_make_post_item("222", "bob", "Comment")]
        html = _make_ssr_html([original_post, dup, dup])
        items = parse_comments_from_html(html, post_pk="111")
        assert len(items) == 1
