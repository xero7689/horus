"""Read-only MCP tools over the local horus database."""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from horus.adapters import list_adapters
from horus.core.dates import parse_since
from horus.mcp.formatting import crawl_params, item_full, item_summary, page_full, page_summary
from horus.mcp.state import McpState

_MAX_LIMIT = 200
_MAX_THREAD_ITEMS = 100
_MAX_PAGE_CHARS = 200_000


def _clamp(limit: int) -> int:
    return max(1, min(limit, _MAX_LIMIT))


def register(server: MCPServer, state: McpState) -> None:
    storage = state.storage

    @server.tool()
    async def list_sites() -> dict[str, Any]:
        """List crawlable sites: whether each needs login, whether a login state
        is saved on this machine, and which crawl parameters it accepts."""
        return {
            "sites": [
                {
                    "site_id": cls.site_id,
                    "display_name": cls.display_name,
                    "requires_login": cls.requires_login,
                    "has_login_state": state.settings.state_path_for(cls.site_id).exists(),
                    "params": crawl_params(cls()),
                }
                for cls in list_adapters()
            ]
        }

    @server.tool()
    async def search(query: str, site: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Full-text search stored posts (CJK supported). Text is previewed;
        call get_item for the full text."""
        items = storage.search(query, site_id=site, limit=_clamp(limit))
        return {"items": [item_summary(i) for i in items]}

    @server.tool()
    async def show(
        site: str | None = None,
        author: str | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List stored posts, newest first, filtered by site / author / since (YYYY-MM-DD)."""
        try:
            since_dt = parse_since(since) if since else None
        except ValueError as e:
            raise ToolError(f"Invalid since: {e}") from e
        items = storage.get_items(
            site_id=site, author_name=author, since=since_dt, limit=_clamp(limit)
        )
        return {"items": [item_summary(i) for i in items]}

    @server.tool()
    async def get_item(site: str, item_id: str, include_thread: bool = False) -> dict[str, Any]:
        """Full text of one stored post.

        include_thread adds the conversation (up to 100 items, text previewed;
        thread_truncated=true if cut). Call get_item on any member for its full text.
        """
        item = storage.get_item(site, item_id)
        if item is None:
            raise ToolError(f"No stored item {item_id!r} for site {site!r}.")
        result: dict[str, Any] = {"item": item_full(item)}
        if include_thread:
            conversation_id = str(item.extra.get("conversation_id") or item.id)
            thread = storage.get_thread_items(site, conversation_id)
            result["thread"] = [item_summary(i) for i in thread[:_MAX_THREAD_ITEMS]]
            result["thread_truncated"] = len(thread) > _MAX_THREAD_ITEMS
        return result

    @server.tool()
    async def pages(site: str | None = None, limit: int = 20) -> dict[str, Any]:
        """List stored web pages (url, title, fetched_at), newest first."""
        stored = storage.get_pages(site_id=site, limit=_clamp(limit))
        return {"pages": [page_summary(p) for p in stored]}

    @server.tool()
    async def get_page(url: str, max_chars: int = 20000) -> dict[str, Any]:
        """Markdown of one stored page, cut at max_chars (at most 200000;
        truncated=true if cut)."""
        page = storage.get_page(url)
        if page is None:
            raise ToolError(
                f"No stored page for {url}. Crawl it first with crawl(site='web', url=...)."
            )
        return page_full(page, max_chars=max(1, min(max_chars, _MAX_PAGE_CHARS)))

    @server.tool()
    async def stats(site: str | None = None) -> dict[str, Any]:
        """Item counts and latest timestamps, overall or for one site."""
        return storage.get_stats(site)
