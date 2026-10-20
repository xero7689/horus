"""Parser tests for the Twitter/X adapter.

All schema-coverage tests run against a REAL captured GraphQL response
(tests/fixtures/twitter/user_tweets.json — anonymous x.com/NASA UserTweets),
per the project rule "avoid hand-writing mock API responses". Only the
robustness test (parser must never raise) uses a synthetic junk entry, since
malformed input is by definition not in a real capture.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from horus.adapters.twitter import TwitterAdapter, _parse_x_time
from horus.models import ScrapedItem

FIXTURE = Path(__file__).parent.parent / "fixtures" / "twitter" / "user_tweets.json"

# Known ids/values observed in the fixture (see plan §0 recon)
PINNED_ID = "2059393717111308634"
RT_ID = "2060549954846814442"  # NASA RT of NASAAdmin
NOTE_ID = "2059352090393371045"  # long tweet w/ note_tweet, inside a self-thread
VIDEO_RT_ID = "2060478778535522321"  # RT carrying a video
VIDEO_BEST_URL = (
    "https://video.twimg.com/amplify_video/2060387213653811202/"
    "vid/avc1/1280x720/u_BTzf1czXaFj3VO.mp4?tag=14"
)
PLAIN_ID = "2060452257406005586"  # plain NASA tweet
CONV_ROOT = "2059334456209555860"  # self-thread conversation root


@pytest.fixture
def body() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def items(body: dict) -> list[ScrapedItem]:
    return TwitterAdapter().parse_response(body)


def _by_id(items: list[ScrapedItem]) -> dict[str, ScrapedItem]:
    return {it.id: it for it in items}


def test_parse_user_tweets_fixture(items: list[ScrapedItem]) -> None:
    # 12 tweet entries + 1 pinned + 5 self-thread items = 18 distinct tweets
    assert len(items) == 18
    assert all(it.id for it in items)
    assert all(it.site_id == "twitter" for it in items)
    # cursors must not become items
    assert all(not it.id.startswith("cursor") for it in items)


def test_pinned_tweet_included(items: list[ScrapedItem]) -> None:
    # Pinned tweet lives in a TimelinePinEntry instruction, not TimelineAddEntries
    assert PINNED_ID in _by_id(items)


def test_author_uses_core_path(items: list[ScrapedItem]) -> None:
    # author moved to core.user_results.result.core; legacy.screen_name is null
    plain = _by_id(items)[PLAIN_ID]
    assert plain.author_name == "NASA"
    assert plain.author_id  # rest_id populated


def test_note_tweet_preferred_over_full_text(items: list[ScrapedItem]) -> None:
    note = _by_id(items)[NOTE_ID]
    # note_tweet.text is the clean authored body (no reply-mention prefix, no t.co tail)
    assert note.text is not None
    assert note.text.startswith("We have awarded")
    assert not note.text.startswith("@")  # full_text starts with @mentions
    assert "t.co" not in note.text


def test_retweet_unwraps_full_content(items: list[ScrapedItem]) -> None:
    rt = _by_id(items)[RT_ID]
    # content comes from the inner retweeted tweet, not the "RT @..." truncated stub
    assert rt.text is not None
    assert not rt.text.startswith("RT @")
    assert rt.text.startswith("We go where we need to be")
    assert len(rt.text) > 1000  # inner note_tweet is ~1266 chars
    # author is the original creator, retweeter recorded separately
    assert rt.author_name == "NASAAdmin"
    assert rt.extra["is_retweet"] is True
    assert rt.extra["retweeted_by"] == "NASA"


def test_retweet_keeps_outer_id_and_timestamp(items: list[ScrapedItem]) -> None:
    rt = _by_id(items)[RT_ID]
    # outer envelope id (dedup-safe, distinct timeline event)
    assert rt.id == RT_ID
    # outer created_at "Sat May 30 02:32:47 +0000 2026"
    assert rt.timestamp.year == 2026
    assert (rt.timestamp.month, rt.timestamp.day) == (5, 30)


def test_self_thread_positional_linking(items: list[ScrapedItem]) -> None:
    by_id = _by_id(items)
    root = by_id[CONV_ROOT]
    second = by_id["2059352090393371045"]
    third = by_id["2059361540826947808"]
    # conversation root: not a reply, no parent
    assert root.extra["is_reply"] is False
    assert root.extra["parent_post_id"] is None
    # subsequent items chain positionally within the module
    assert second.extra["is_reply"] is True
    assert second.extra["parent_post_id"] == CONV_ROOT
    assert third.extra["parent_post_id"] == "2059352090393371045"
    # all share the conversation id from legacy.conversation_id_str
    for tid in (CONV_ROOT, "2059352090393371045", "2059361540826947808"):
        assert by_id[tid].extra["conversation_id"] == CONV_ROOT


def test_media_video_picks_max_mp4_bitrate(items: list[ScrapedItem]) -> None:
    vid = _by_id(items)[VIDEO_RT_ID]
    assert vid.extra["media_type"] == "video"
    assert vid.extra["media_urls"] == [VIDEO_BEST_URL]
    # HLS variant (application/x-mpegURL) must be skipped
    assert all(".m3u8" not in u for u in vid.extra["media_urls"])


def test_parse_never_raises_on_unknown_entry() -> None:
    junk = {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [
                                {
                                    "type": "TimelineAddEntries",
                                    "entries": [
                                        {"entryId": "who-to-follow-1", "content": {}},
                                        {"entryId": "tweet-1", "content": {}},  # no itemContent
                                        {
                                            "entryId": "tweet-2",
                                            "content": {
                                                "itemContent": {
                                                    "tweet_results": {
                                                        "result": {"__typename": "TweetTombstone"}
                                                    }
                                                }
                                            },
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    # must not raise, and must skip every malformed/unknown entry
    assert TwitterAdapter().parse_response(junk) == []


def test_created_at_parsing() -> None:
    dt = _parse_x_time("Sat May 30 02:32:47 +0000 2026")
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None  # tz-aware, comparable to `since`
    assert (dt.year, dt.month, dt.day) == (2026, 5, 30)


def test_empty_body_returns_empty() -> None:
    assert TwitterAdapter().parse_response({}) == []
