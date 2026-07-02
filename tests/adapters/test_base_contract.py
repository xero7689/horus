"""Locks the SiteAdapter contract: page/http adapters need no interception stubs."""

from collections.abc import Callable
from typing import Any

from horus.adapters.base import SiteAdapter


class _PageOnly(SiteAdapter):
    site_id = "page_only"
    display_name = "Page Only"
    login_url = "https://example.com/login"
    requires_login = False
    has_page_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return ["https://example.com"]


def test_page_adapter_needs_no_interception_stubs() -> None:
    a = _PageOnly()  # must instantiate without overriding interception methods
    assert a.parse_response({}) == []
    f: Callable[[str, dict[str, Any]], bool] = a.get_response_filter()
    assert f("https://example.com", {}) is False


def test_page_mode_hooks_declared_with_none_defaults() -> None:
    assert SiteAdapter.wait_for_selector is None
    assert SiteAdapter.page_script is None
    assert SiteAdapter.content_script is None
