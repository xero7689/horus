"""Integration-level unit test: crawl with --with-comments triggers comment fetch."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from horus.adapters.threads import parse_comments_from_html
from horus.cli import main
from horus.models import ScrapedItem


def _post(pk: str, is_reply: bool = False) -> ScrapedItem:
    return ScrapedItem(
        id=pk,
        site_id="threads",
        url=f"https://www.threads.net/@alice/post/{pk}",
        text="post text",
        author_name="alice",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        extra={"is_reply": is_reply, "parent_post_id": None, "conversation_id": None},
    )


class TestCrawlWithComments:
    def test_with_comments_flag_triggers_comment_scrape(self, tmp_path) -> None:
        """When --with-comments is passed, scrape_comments() is called for each non-reply post."""
        post1 = _post("p1")
        post2 = _post("p2")
        comment1 = _post("c1", is_reply=True)

        scrape_comments_calls: list[dict] = []

        async def fake_scrape(
            url, response_filter, parser, state_path, *, since=None, on_progress=None, on_batch=None
        ):
            if on_batch:
                on_batch([post1, post2])
            return [post1, post2]

        async def fake_scrape_comments(url, post_pk, parser, state_path=None):
            scrape_comments_calls.append({"url": url, "post_pk": post_pk, "parser": parser})
            return [comment1]

        # Build a mock adapter that has get_comment_parser()
        mock_adapter = MagicMock()
        mock_adapter.get_response_filter.return_value = lambda url, body: False
        mock_adapter.parse_response.return_value = []
        mock_adapter.get_urls.return_value = ["https://www.threads.net/@alice"]
        mock_adapter.post_process.side_effect = lambda x: x
        mock_adapter.get_comment_parser.return_value = parse_comments_from_html

        mock_adapter_cls = MagicMock()
        mock_adapter_cls.return_value = mock_adapter
        mock_adapter_cls.has_http_mode = False
        mock_adapter_cls.has_page_mode = False
        mock_adapter_cls.requires_login = False

        runner = CliRunner()
        with (
            patch("horus.cli.get_adapter", return_value=mock_adapter_cls),
            patch("horus.cli.BaseScraper") as MockScraper,
            patch("horus.cli._get_storage") as mock_storage_fn,
            patch("horus.cli._get_settings") as mock_settings_fn,
        ):
            mock_scraper_inst = AsyncMock()
            mock_scraper_inst.__aenter__ = AsyncMock(return_value=mock_scraper_inst)
            mock_scraper_inst.__aexit__ = AsyncMock(return_value=False)
            mock_scraper_inst.scrape = AsyncMock(side_effect=fake_scrape)
            mock_scraper_inst.scrape_comments = AsyncMock(side_effect=fake_scrape_comments)
            MockScraper.return_value = mock_scraper_inst

            mock_storage = MagicMock()
            mock_storage.get_latest_timestamp.return_value = None
            mock_storage.upsert_items.return_value = 1
            mock_storage_fn.return_value = mock_storage

            mock_settings = MagicMock()
            mock_settings.headless = True
            mock_settings.scroll_delay_min = 1.0
            mock_settings.scroll_delay_max = 2.0
            mock_settings.request_jitter = 0.5
            mock_settings.max_pages = 5
            mock_settings.state_path_for.return_value = tmp_path / "state.json"
            mock_settings_fn.return_value = mock_settings

            result = runner.invoke(
                main,
                ["crawl", "threads", "--user", "alice", "--with-comments"],
            )

        # scrape_comments must be called once per non-reply post
        assert len(scrape_comments_calls) == 2, (
            f"Expected 2 calls, got {len(scrape_comments_calls)}. Output: {result.output}"
        )
        post_pks = {c["post_pk"] for c in scrape_comments_calls}
        assert post_pks == {"p1", "p2"}
        # Verify correct parser was passed
        for call in scrape_comments_calls:
            assert call["parser"] is parse_comments_from_html
