from datetime import UTC, datetime

from horus.adapters.serper import SerperAdapter
from horus.adapters.threads import ThreadsAdapter
from horus.adapters.web import GenericWebAdapter
from horus.mcp.formatting import (
    TEXT_PREVIEW_CHARS,
    crawl_params,
    item_full,
    item_summary,
    page_full,
    page_summary,
)
from horus.models import ScrapedItem, ScrapedPage

_TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _item(text: str | None) -> ScrapedItem:
    return ScrapedItem(
        id="1",
        site_id="threads",
        url="https://e.com/1",
        text=text,
        author_name="alice",
        timestamp=_TS,
        extra={"like_count": 3},
    )


def test_item_summary_truncates_long_text() -> None:
    d = item_summary(_item("字" * (TEXT_PREVIEW_CHARS + 10)))
    assert len(d["text"]) == TEXT_PREVIEW_CHARS
    assert d["text_truncated"] is True
    assert d["timestamp"] == _TS.isoformat()
    assert d["extra"] == {"like_count": 3}


def test_item_summary_short_and_missing_text() -> None:
    assert item_summary(_item("hi"))["text_truncated"] is False
    d = item_summary(_item(None))
    assert d["text"] == "" and d["text_truncated"] is False


def test_item_full_keeps_whole_text() -> None:
    d = item_full(_item("x" * 2000))
    assert len(d["text"]) == 2000
    assert "text_truncated" not in d


def test_page_summary_and_full() -> None:
    page = ScrapedPage(
        url="https://e.com", site_id="web", title="T", markdown="m" * 50, fetched_at=_TS
    )
    assert page_summary(page) == {
        "url": "https://e.com",
        "title": "T",
        "fetched_at": _TS.isoformat(),
    }
    full = page_full(page, max_chars=10)
    assert full["markdown"] == "m" * 10 and full["truncated"] is True
    assert page_full(page, max_chars=100)["truncated"] is False


def test_crawl_params_maps_names_and_hides_unexposed() -> None:
    names = [p["name"] for p in crawl_params(ThreadsAdapter())]
    assert set(names) == {"user", "url", "mode", "with_comments"}
    web = [p["name"] for p in crawl_params(GenericWebAdapter())]
    assert web == ["url"]  # --url-list is a local file path, not exposed
    serper = {p["name"] for p in crawl_params(SerperAdapter())}
    assert serper == {"query", "gl", "hl"}
