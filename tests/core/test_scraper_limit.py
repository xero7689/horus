"""Pure helpers behind BaseScraper.scrape()'s limit-based early stop."""

from datetime import UTC, datetime

from horus.core.scraper import count_new_items, limit_reached
from horus.models import ScrapedItem


def _item(item_id: str, day: int) -> ScrapedItem:
    return ScrapedItem(
        id=item_id,
        site_id="t",
        url=f"https://e.com/{item_id}",
        timestamp=datetime(2026, 1, day, tzinfo=UTC),
    )


def test_counts_unseen_items_and_records_them() -> None:
    seen: set[str] = set()
    assert count_new_items([_item("a", 1), _item("b", 2)], seen, since=None) == 2
    assert seen == {"a", "b"}


def test_duplicate_ids_are_counted_once() -> None:
    seen: set[str] = set()
    count_new_items([_item("a", 1)], seen, since=None)
    assert count_new_items([_item("a", 1), _item("c", 3)], seen, since=None) == 1


def test_items_at_or_before_since_are_not_counted() -> None:
    seen: set[str] = set()
    since = datetime(2026, 1, 2, tzinfo=UTC)
    assert count_new_items([_item("a", 1), _item("b", 2), _item("c", 3)], seen, since=since) == 1


def test_limit_reached() -> None:
    assert limit_reached(10, 10) is True
    assert limit_reached(11, 10) is True
    assert limit_reached(9, 10) is False


def test_limit_zero_means_unlimited() -> None:
    assert limit_reached(10_000, 0) is False
