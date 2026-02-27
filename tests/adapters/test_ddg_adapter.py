from datetime import UTC

import pytest

from horus.adapters.ddg import DuckDuckGoAdapter

# ---------------------------------------------------------------------------
# Sample HTML fixture
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html>
<body>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a class="result__a" href="https://www.example.com/python-crawler">
        Python Crawler Tutorial
      </a>
    </h2>
    <div class="result__extras">
      <div class="result__extras__url">
        <a class="result__url" href="https://www.example.com/python-crawler">
          www.example.com/python-crawler
        </a>
      </div>
    </div>
    <div class="result__snippet">
      Learn how to build a web crawler in Python using requests and BeautifulSoup.
    </div>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a class="result__a" href="https://scrapy.org/">
        Scrapy - An open source web scraping framework
      </a>
    </h2>
    <div class="result__extras">
      <div class="result__extras__url">
        <a class="result__url" href="https://scrapy.org/">
          scrapy.org
        </a>
      </div>
    </div>
    <div class="result__snippet">
      Open-source framework for efficient web scraping and data extraction.
    </div>
  </div>
</div>
<div class="result result--sep"><!-- separator --></div>
</body>
</html>
"""

_EMPTY_HTML = """<html><body><div class="no-results">No results found.</div></body></html>"""

_RESULT_WITHOUT_SNIPPET = """
<html>
<body>
<div class="result results_links web-result">
  <div class="links_main result__body">
    <h2 class="result__title">
      <a class="result__a" href="https://example.org/">
        Example Site
      </a>
    </h2>
  </div>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------


class TestDuckDuckGoAdapterMetadata:
    def test_site_id(self) -> None:
        assert DuckDuckGoAdapter.site_id == "ddg"

    def test_display_name(self) -> None:
        assert "DuckDuckGo" in DuckDuckGoAdapter.display_name

    def test_requires_no_login(self) -> None:
        assert DuckDuckGoAdapter.requires_login is False

    def test_has_http_mode(self) -> None:
        assert DuckDuckGoAdapter.has_http_mode is True

    def test_has_page_mode_false(self) -> None:
        assert DuckDuckGoAdapter.has_page_mode is False

    def test_response_filter_always_false(self) -> None:
        adapter = DuckDuckGoAdapter()
        f = adapter.get_response_filter()
        assert f("https://duckduckgo.com/anything", {}) is False

    def test_parse_response_returns_empty(self) -> None:
        adapter = DuckDuckGoAdapter()
        assert adapter.parse_response({}) == []


# ---------------------------------------------------------------------------
# URL generation
# ---------------------------------------------------------------------------


class TestGetUrls:
    def test_query_generates_url(self) -> None:
        adapter = DuckDuckGoAdapter()
        urls = adapter.get_urls(query="python crawler")
        assert len(urls) == 1
        assert "html.duckduckgo.com" in urls[0]

    def test_no_query_raises(self) -> None:
        adapter = DuckDuckGoAdapter()
        with pytest.raises(ValueError, match="--query"):
            adapter.get_urls()

    def test_crawl_options_has_query(self) -> None:
        adapter = DuckDuckGoAdapter()
        opts = adapter.get_crawl_options()
        names = [o["name"] for o in opts]
        assert "--query" in names


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


class TestParseHtml:
    def test_parses_two_results(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        assert len(items) == 2

    def test_result_fields(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        item = items[0]
        assert item.site_id == "ddg"
        assert item.url == "https://www.example.com/python-crawler"
        assert item.text == "Python Crawler Tutorial"
        assert item.timestamp.tzinfo == UTC

    def test_snippet_in_extra(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        assert "BeautifulSoup" in items[0].extra["snippet"]

    def test_rank_in_extra(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        assert items[0].extra["rank"] == 1
        assert items[1].extra["rank"] == 2

    def test_query_in_extra(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        assert items[0].extra["query"] == "python crawler"

    def test_separator_divs_excluded(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        # result--sep should not be included
        assert len(items) == 2

    def test_empty_page_returns_empty(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_EMPTY_HTML, query="test")
        assert items == []

    def test_result_without_snippet(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_RESULT_WITHOUT_SNIPPET, query="test")
        assert len(items) == 1
        assert items[0].extra["snippet"] is None

    def test_item_id_is_stable(self) -> None:
        """Same URL + query should produce the same ID."""
        adapter = DuckDuckGoAdapter()
        items1 = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        items2 = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        assert items1[0].id == items2[0].id

    def test_second_result_url(self) -> None:
        adapter = DuckDuckGoAdapter()
        items = adapter.parse_html(_SAMPLE_HTML, query="python crawler")
        assert items[1].url == "https://scrapy.org/"
