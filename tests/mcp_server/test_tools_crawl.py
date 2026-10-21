import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from horus.adapters.base import SiteAdapter
from horus.mcp.state import McpState
from horus.models import ScrapedItem
from tests.mcp_server.conftest import call, call_error


class _FakeSearch(SiteAdapter):
    site_id = "fake_search"
    display_name = "Fake Search"
    login_url = ""
    requires_login = False
    has_http_mode = True
    gate: asyncio.Event | None = None
    fail_with: Exception | None = None

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []

    def get_crawl_options(self) -> list[dict[str, Any]]:
        return [{"name": "--query", "help": "q", "required": True, "default": None}]

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        if type(self).gate is not None:
            await type(self).gate.wait()
        if type(self).fail_with is not None:
            raise type(self).fail_with
        return [
            ScrapedItem(
                id=f"r{i}",
                site_id="fake_search",
                url=f"https://e.com/{i}",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            )
            for i in range(3)
        ]


class _NeedsLogin(SiteAdapter):
    site_id = "fake_login"
    display_name = "Fake Login"
    login_url = ""
    requires_login = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return ["https://e.com/x"]


class _BadUrlList(SiteAdapter):
    site_id = "fake_bad"
    display_name = "Fake Bad"
    login_url = ""
    requires_login = False

    def get_urls(self, **kwargs: Any) -> list[str]:
        raise IsADirectoryError("[Errno 21] Is a directory: '/tmp'")


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSearch.gate = None
    _FakeSearch.fail_with = None
    table: dict[str, type[SiteAdapter]] = {
        "fake_search": _FakeSearch,
        "fake_login": _NeedsLogin,
        "fake_bad": _BadUrlList,
    }

    def fake_get_adapter(site: str) -> type[SiteAdapter]:
        if site not in table:
            raise ValueError(f"Unknown site: {site}. Available: {sorted(table)}")
        return table[site]

    monkeypatch.setattr("horus.core.crawl_plan.get_adapter", fake_get_adapter)
    monkeypatch.setattr("horus.mcp.tools_crawl.get_adapter", fake_get_adapter)


async def test_crawl_finishes_within_wait(client) -> None:
    res = await call(client, "crawl", site="fake_search", query="q")
    assert res["status"] == "done"
    assert res["items_found"] == 3 and res["refs"] == ["r0", "r1", "r2"]


async def test_crawl_goes_background_then_status_reports_done(client) -> None:
    _FakeSearch.gate = asyncio.Event()
    res = await call(client, "crawl", site="fake_search", query="q", wait_seconds=0)
    assert res["status"] == "running"
    _FakeSearch.gate.set()
    for _ in range(50):
        status = await call(client, "crawl_status", job_id=res["job_id"])
        if status["status"] == "done":
            break
        await asyncio.sleep(0.01)
    assert status["status"] == "done" and status["items_found"] == 3


async def test_same_site_twice_is_rejected_with_job_id(client) -> None:
    _FakeSearch.gate = asyncio.Event()
    first = await call(client, "crawl", site="fake_search", query="q", wait_seconds=0)
    err = await call_error(client, "crawl", site="fake_search", query="q2", wait_seconds=0)
    assert first["job_id"] in err
    _FakeSearch.gate.set()


async def test_failure_within_wait_is_tool_error(client) -> None:
    _FakeSearch.fail_with = RuntimeError("upstream 500")
    err = await call_error(client, "crawl", site="fake_search", query="q")
    assert "RuntimeError: upstream 500" in err


async def test_missing_inputs_lists_params(client) -> None:
    err = await call_error(client, "crawl", site="fake_search")
    assert "query" in err


async def test_unknown_site(client) -> None:
    assert "Unknown site" in await call_error(client, "crawl", site="nope", query="q")


async def test_non_value_error_from_plan_names_the_cause(client) -> None:
    err = await call_error(client, "crawl", site="fake_bad", url="/tmp")
    assert "IsADirectoryError: [Errno 21] Is a directory" in err


async def test_missing_login_state_says_run_login(client) -> None:
    err = await call_error(client, "crawl", site="fake_login", url="https://e.com/x")
    assert "horus login fake_login" in err


async def test_bad_since_and_wait_seconds(client) -> None:
    bad_since = await call_error(client, "crawl", site="fake_search", query="q", since="x")
    assert "since" in bad_since.lower()
    bad_wait = await call_error(client, "crawl", site="fake_search", query="q", wait_seconds=601)
    assert "wait_seconds" in bad_wait


async def test_cancel_and_unknown_job(client) -> None:
    _FakeSearch.gate = asyncio.Event()
    res = await call(client, "crawl", site="fake_search", query="q", wait_seconds=0)
    assert (await call(client, "crawl_cancel", job_id=res["job_id"]))["cancelled"] is True
    assert "restart" in await call_error(client, "crawl_status", job_id="deadbeef")


async def test_shutdown_cancels_running_jobs(client, state: McpState) -> None:
    _FakeSearch.gate = asyncio.Event()
    res = await call(client, "crawl", site="fake_search", query="q", wait_seconds=0)
    await state.shutdown()
    job = state.jobs.get(res["job_id"])
    assert job is not None and job.status.value == "cancelled"
