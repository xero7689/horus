from datetime import UTC, datetime

from horus.mcp.state import McpState
from horus.models import ScrapedItem, ScrapedPage
from tests.mcp_server.conftest import call, call_error


def _seed(state: McpState) -> None:
    state.storage.upsert_items(
        [
            ScrapedItem(
                id="root",
                site_id="threads",
                url="https://e.com/root",
                text="hello world 你好世界",
                author_name="alice",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                extra={"conversation_id": "root"},
            ),
            ScrapedItem(
                id="reply",
                site_id="threads",
                url="https://e.com/reply",
                text="a reply",
                author_name="bob",
                timestamp=datetime(2026, 1, 2, tzinfo=UTC),
                extra={"is_reply": True, "conversation_id": "root", "parent_post_id": "root"},
            ),
        ]
    )
    state.storage.upsert_page(
        ScrapedPage(url="https://e.com/p", site_id="web", title="P", markdown="# body")
    )


async def test_list_sites_reports_login_state(client, state: McpState) -> None:
    state.settings.state_path_for("threads").write_text("{}")
    sites = {s["site_id"]: s for s in (await call(client, "list_sites"))["sites"]}
    assert sites["threads"]["has_login_state"] is True
    assert sites["instagram"]["has_login_state"] is False
    assert sites["instagram"]["requires_login"] is True
    assert {p["name"] for p in sites["serper"]["params"]} == {"query", "gl", "hl"}


async def test_search_and_show(client, state: McpState) -> None:
    _seed(state)
    found = (await call(client, "search", query="hello"))["items"]
    assert [i["id"] for i in found] == ["root"]
    shown = (await call(client, "show", site="threads", author="bob"))["items"]
    assert [i["id"] for i in shown] == ["reply"]


async def test_show_rejects_bad_since(client) -> None:
    assert "since" in (await call_error(client, "show", since="yesterday")).lower()


async def test_get_item_with_thread_uses_conversation_id(client, state: McpState) -> None:
    _seed(state)
    res = await call(client, "get_item", site="threads", item_id="reply", include_thread=True)
    assert res["item"]["id"] == "reply"
    assert {i["id"] for i in res["thread"]} == {"root", "reply"}


async def test_get_item_not_found(client) -> None:
    assert "nope" in await call_error(client, "get_item", site="threads", item_id="nope")


async def test_pages_and_get_page(client, state: McpState) -> None:
    _seed(state)
    assert (await call(client, "pages"))["pages"][0]["url"] == "https://e.com/p"
    page = await call(client, "get_page", url="https://e.com/p", max_chars=3)
    assert page["markdown"] == "# b" and page["truncated"] is True
    missing = await call_error(client, "get_page", url="https://e.com/missing")
    assert "https://e.com/missing" in missing


async def test_stats(client, state: McpState) -> None:
    _seed(state)
    assert isinstance(await call(client, "stats"), dict)
