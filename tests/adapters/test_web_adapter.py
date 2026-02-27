import pytest

from horus.adapters.web import GenericWebAdapter


@pytest.fixture
def adapter() -> GenericWebAdapter:
    return GenericWebAdapter()


class TestGenericWebAdapter:
    def test_site_id(self) -> None:
        assert GenericWebAdapter.site_id == "web"

    def test_requires_no_login(self) -> None:
        assert GenericWebAdapter.requires_login is False

    def test_has_page_mode(self) -> None:
        assert GenericWebAdapter.has_page_mode is True

    def test_response_filter_always_false(self, adapter: GenericWebAdapter) -> None:
        f = adapter.get_response_filter()
        assert f("https://example.com/api", {"data": "anything"}) is False
        assert f("https://threads.net/graphql", {"mediaData": {}}) is False

    def test_parse_response_returns_empty(self, adapter: GenericWebAdapter) -> None:
        result = adapter.parse_response({"some": "body"})
        assert result == []

    def test_get_urls_single_url(self, adapter: GenericWebAdapter) -> None:
        urls = adapter.get_urls(url="https://example.com")
        assert urls == ["https://example.com"]

    def test_get_urls_requires_url_or_url_list(self, adapter: GenericWebAdapter) -> None:
        with pytest.raises(ValueError, match="requires --url or --url-list"):
            adapter.get_urls()

    def test_get_urls_from_file(
        self, adapter: GenericWebAdapter, tmp_path: pytest.TempPathFactory
    ) -> None:  # noqa: E501
        url_file = tmp_path / "urls.txt"  # type: ignore[operator]
        url_file.write_text("https://example.com\nhttps://other.com\n# comment\n")
        urls = adapter.get_urls(url_list=str(url_file))
        assert urls == ["https://example.com", "https://other.com"]

    def test_get_urls_file_not_found(self, adapter: GenericWebAdapter) -> None:
        with pytest.raises(ValueError, match="not found"):
            adapter.get_urls(url_list="/nonexistent/file.txt")

    def test_get_urls_empty_file(
        self, adapter: GenericWebAdapter, tmp_path: pytest.TempPathFactory
    ) -> None:  # noqa: E501
        url_file = tmp_path / "urls.txt"  # type: ignore[operator]
        url_file.write_text("# only comments\n")
        with pytest.raises(ValueError, match="No URLs found"):
            adapter.get_urls(url_list=str(url_file))

    def test_get_crawl_options(self, adapter: GenericWebAdapter) -> None:
        opts = adapter.get_crawl_options()
        names = [o["name"] for o in opts]
        assert "--url" in names
        assert "--url-list" in names
