from datetime import UTC, datetime

from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


def _item(
    id: str = "1",
    site_id: str = "threads",
    text: str = "hello world",
    author_name: str = "alice",
    ts: str = "2024-01-01T10:00:00+00:00",
) -> ScrapedItem:
    return ScrapedItem(
        id=id,
        site_id=site_id,
        url=f"https://example.com/{id}",
        text=text,
        author_id=f"uid_{id}",
        author_name=author_name,
        timestamp=datetime.fromisoformat(ts),
        extra={"like_count": 5},
    )


class TestUpsertItems:
    def test_returns_new_count(self, storage: HorusStorage) -> None:
        items = [_item("1"), _item("2")]
        count = storage.upsert_items(items)
        assert count == 2

    def test_dedup_same_id(self, storage: HorusStorage) -> None:
        storage.upsert_items([_item("1")])
        count = storage.upsert_items([_item("1")])
        assert count == 0

    def test_update_on_conflict(self, storage: HorusStorage) -> None:
        storage.upsert_items([_item("1", text="old")])
        storage.upsert_items([_item("1", text="new")])
        items = storage.get_items()
        assert items[0].text == "new"

    def test_cross_site_no_conflict(self, storage: HorusStorage) -> None:
        count = storage.upsert_items([
            _item("1", site_id="threads"),
            _item("1", site_id="instagram"),
        ])
        assert count == 2


class TestGetItems:
    def test_filter_by_site(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", site_id="threads"),
            _item("2", site_id="instagram"),
        ])
        results = storage.get_items(site_id="threads")
        assert len(results) == 1
        assert results[0].site_id == "threads"

    def test_filter_by_author(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", author_name="alice"),
            _item("2", author_name="bob"),
        ])
        results = storage.get_items(author_name="alice")
        assert len(results) == 1
        assert results[0].author_name == "alice"

    def test_filter_by_since(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", ts="2024-01-01T00:00:00+00:00"),
            _item("2", ts="2024-06-01T00:00:00+00:00"),
        ])
        since = datetime(2024, 3, 1, tzinfo=UTC)
        results = storage.get_items(since=since)
        assert len(results) == 1
        assert results[0].id == "2"

    def test_limit(self, storage: HorusStorage) -> None:
        storage.upsert_items([_item(str(i)) for i in range(10)])
        results = storage.get_items(limit=3)
        assert len(results) == 3

    def test_sorted_descending(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", ts="2024-01-01T00:00:00+00:00"),
            _item("2", ts="2024-06-01T00:00:00+00:00"),
        ])
        results = storage.get_items()
        assert results[0].id == "2"  # newer first


class TestGetLatestTimestamp:
    def test_returns_none_when_empty(self, storage: HorusStorage) -> None:
        assert storage.get_latest_timestamp("threads") is None

    def test_returns_max_timestamp(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", ts="2024-01-01T00:00:00+00:00"),
            _item("2", ts="2024-06-01T00:00:00+00:00"),
        ])
        ts = storage.get_latest_timestamp("threads")
        assert ts is not None
        assert ts.year == 2024
        assert ts.month == 6

    def test_filter_by_author(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", author_name="alice", ts="2024-01-01T00:00:00+00:00"),
            _item("2", author_name="bob", ts="2024-06-01T00:00:00+00:00"),
        ])
        ts = storage.get_latest_timestamp("threads", author_name="alice")
        assert ts is not None
        assert ts.month == 1


class TestSearch:
    def test_fts_basic(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", text="playwright automation"),
            _item("2", text="something else"),
        ])
        results = storage.search("playwright")
        assert len(results) == 1
        assert results[0].id == "1"

    def test_fts_chinese_trigram(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", text="違憲審查的討論"),
            _item("2", text="完全無關的內容"),
        ])
        results = storage.search("違憲審")
        assert len(results) == 1
        assert results[0].id == "1"

    def test_short_query_fallback_like(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", text="hi there"),
            _item("2", text="bye now"),
        ])
        results = storage.search("hi")
        assert len(results) == 1

    def test_filter_by_site(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", site_id="threads", text="playwright test"),
            _item("2", site_id="instagram", text="playwright test"),
        ])
        results = storage.search("playwright", site_id="threads")
        assert len(results) == 1
        assert results[0].site_id == "threads"

    def test_no_results(self, storage: HorusStorage) -> None:
        storage.upsert_items([_item("1", text="hello")])
        results = storage.search("zzznomatch")
        assert results == []


class TestLogCrawl:
    def test_log_crawl(self, storage: HorusStorage) -> None:
        started = datetime(2024, 1, 1, tzinfo=UTC)
        storage.log_crawl("threads", "https://example.com", 10, 5, started)
        # No assertion needed — just checking it doesn't raise


class TestGetStats:
    def test_empty(self, storage: HorusStorage) -> None:
        stats = storage.get_stats()
        assert stats["total"] == 0

    def test_total(self, storage: HorusStorage) -> None:
        storage.upsert_items([_item("1"), _item("2")])
        stats = storage.get_stats()
        assert stats["total"] == 2

    def test_by_site(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", site_id="threads"),
            _item("2", site_id="threads"),
            _item("3", site_id="instagram"),
        ])
        stats = storage.get_stats()
        assert stats["by_site"]["threads"] == 2
        assert stats["by_site"]["instagram"] == 1

    def test_filter_by_site(self, storage: HorusStorage) -> None:
        storage.upsert_items([
            _item("1", site_id="threads"),
            _item("2", site_id="instagram"),
        ])
        stats = storage.get_stats(site_id="threads")
        assert stats["total"] == 1
        assert "threads" in stats["by_site"]
        assert "instagram" not in stats["by_site"]
