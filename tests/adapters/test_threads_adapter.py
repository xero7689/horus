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
