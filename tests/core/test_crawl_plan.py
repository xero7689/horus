"""resolve_crawl_plan: the shared crawl prelude (adapter, URLs, login state)."""

from pathlib import Path
from typing import Any

import pytest

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.crawl_plan import resolve_crawl_plan


class _Intercept(SiteAdapter):
    site_id = "plan_intercept"
    display_name = "Plan Intercept"
    login_url = ""
    requires_login = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        if not kwargs.get("user"):
            raise ValueError("plan_intercept requires --user")
        return [f"https://e.com/{kwargs['user']}"]


class _Http(SiteAdapter):
    site_id = "plan_http"
    display_name = "Plan Http"
    login_url = ""
    requires_login = False
    has_http_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        raise AssertionError("get_urls must not be called for http mode")


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    table = {"plan_intercept": _Intercept, "plan_http": _Http}

    def fake_get_adapter(site: str) -> type[SiteAdapter]:
        if site not in table:
            raise ValueError(f"Unknown site: {site}")
        return table[site]

    monkeypatch.setattr("horus.core.crawl_plan.get_adapter", fake_get_adapter)


def _settings(tmp_path: Path) -> Settings:
    return Settings(base_dir=tmp_path, _env_file=None)


def test_unknown_site_raises_value_error(registry: None, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown site"):
        resolve_crawl_plan("nope", {}, _settings(tmp_path))


def test_missing_params_propagate_adapter_error(registry: None, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --user"):
        resolve_crawl_plan("plan_intercept", {}, _settings(tmp_path))


def test_http_mode_skips_get_urls(registry: None, tmp_path: Path) -> None:
    plan = resolve_crawl_plan("plan_http", {"query": "x"}, _settings(tmp_path))
    assert plan.urls == []
    assert plan.site == "plan_http"


def test_login_state_missing_when_required_and_absent(registry: None, tmp_path: Path) -> None:
    plan = resolve_crawl_plan("plan_intercept", {"user": "a"}, _settings(tmp_path))
    assert plan.urls == ["https://e.com/a"]
    assert plan.state_path is None
    assert plan.login_state_missing is True


def test_existing_state_is_used(registry: None, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    settings.state_path_for("plan_intercept").write_text("{}")
    plan = resolve_crawl_plan("plan_intercept", {"user": "a"}, settings)
    assert plan.state_path == settings.state_path_for("plan_intercept")
    assert plan.login_state_missing is False


def test_use_saved_state_false_never_reports_missing(registry: None, tmp_path: Path) -> None:
    plan = resolve_crawl_plan(
        "plan_intercept", {"user": "a"}, _settings(tmp_path), use_saved_state=False
    )
    assert plan.state_path is None
    assert plan.login_state_missing is False
