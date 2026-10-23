"""Build and run the horus MCP server (stdio transport)."""

from mcp.server.mcpserver import MCPServer

from horus.adapters._io import disable_stdin
from horus.config import Settings
from horus.mcp import tools_crawl, tools_query
from horus.mcp.state import McpState, create_state

_INSTRUCTIONS = (
    "Horus: query and crawl social/web content stored in a local SQLite DB. "
    "Call list_sites first to see which sites can be crawled now. crawl waits up "
    "to wait_seconds; if it returns status=running, poll crawl_status(job_id), "
    "then read results with search / show / get_item / pages / get_page."
)


def build_server(state: McpState) -> MCPServer:
    server = MCPServer("horus", instructions=_INSTRUCTIONS)
    tools_query.register(server, state)
    tools_crawl.register(server, state)
    return server


async def run_stdio(settings: Settings) -> None:
    """Serve over stdio until the client closes stdin, then cancel running crawls."""
    disable_stdin()
    settings.ensure_dirs()
    state = create_state(settings)
    try:
        await build_server(state).run_stdio_async()
    finally:
        await state.shutdown()
