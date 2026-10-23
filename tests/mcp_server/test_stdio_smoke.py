"""End-to-end: spawn `horus mcp` over stdio, handshake, list tools."""

import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "list_sites",
    "search",
    "show",
    "get_item",
    "pages",
    "get_page",
    "stats",
    "crawl",
    "crawl_status",
    "crawl_cancel",
}


async def test_stdio_handshake_and_tool_list(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from horus.cli import main; main()", "mcp"],
        env={"HORUS_BASE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
    assert {t.name for t in tools.tools} == EXPECTED_TOOLS
