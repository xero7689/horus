"""Unit tests for BaseScraper.scrape_comments() using a mock page."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from horus.core.scraper import BaseScraper
from horus.models import ScrapedItem


def _make_item(pk: str) -> ScrapedItem:
    return ScrapedItem(
        id=pk,
        site_id="threads",
        url=f"https://www.threads.net/post/{pk}",
        text="test",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


class TestScrapeComments:
    @pytest.mark.asyncio
    async def test_calls_parser_with_html_and_returns_items(self) -> None:
        """scrape_comments() should pass page HTML to parser and return results."""
        expected_items = [_make_item("111"), _make_item("222")]
        mock_html = "<html>mock content</html>"

        def fake_parser(html: str, *, post_pk: str) -> list[ScrapedItem]:
            assert html == mock_html
            assert post_pk == "abc"
            return expected_items

        scraper = BaseScraper()
        scraper._browser = MagicMock()  # pretend we're inside context manager

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.content = AsyncMock(return_value=mock_html)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(
            scraper, "new_context", new_callable=AsyncMock, return_value=mock_context
        ):
            items = await scraper.scrape_comments(
                url="https://www.threads.com/@alice/post/abc",
                post_pk="abc",
                parser=fake_parser,
                state_path=None,
            )

        assert items == expected_items
        mock_page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_parser_returns_nothing(self) -> None:
        scraper = BaseScraper()
        scraper._browser = MagicMock()

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html></html>")

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        with patch.object(
            scraper, "new_context", new_callable=AsyncMock, return_value=mock_context
        ):
            items = await scraper.scrape_comments(
                url="https://www.threads.com/@alice/post/abc",
                post_pk="abc",
                parser=lambda html, *, post_pk: [],
                state_path=None,
            )

        assert items == []
