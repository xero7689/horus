"""Fixtures: an in-memory MCP client wired to a temp-DB server state."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from horus.config import Settings
from horus.core.storage import HorusStorage
from horus.mcp.server import build_server
from horus.mcp.state import McpState, create_state


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(base_dir=tmp_path, _env_file=None)
    s.ensure_dirs()
    return s


@pytest.fixture
async def state(settings: Settings, tmp_path: Path) -> AsyncIterator[McpState]:
    db = tmp_path / "t.db"
    st = create_state(settings, storage_factory=lambda: HorusStorage(db))
    yield st
    await st.shutdown()  # cancel leftover jobs so no task outlives the test


@pytest.fixture
async def client(state: McpState) -> AsyncIterator[Client]:
    """Hold the Client in its own task.

    anyio cancel scopes must be exited by the task that entered them, but
    pytest-asyncio runs fixture setup and teardown in different tasks; a plain
    `async with Client(...): yield` errors at every teardown.
    """
    ready: asyncio.Future[Client] = asyncio.get_running_loop().create_future()
    stop = asyncio.Event()

    async def hold() -> None:
        async with Client(build_server(state)) as c:
            ready.set_result(c)
            await stop.wait()

    task = asyncio.create_task(hold())
    await asyncio.wait({ready, task}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        task.result()  # re-raise a failure while entering the client
    yield ready.result()
    stop.set()
    await task


async def call(client: Client, tool: str, **args: Any) -> Any:
    """Call a tool; return parsed JSON, or raise AssertionError carrying the error text."""
    result = await client.call_tool(tool, args)
    text = result.content[0].text  # type: ignore[union-attr]
    if result.is_error:
        raise AssertionError(text)
    return json.loads(text)


async def call_error(client: Client, tool: str, **args: Any) -> str:
    """Call a tool expected to fail; return its error text."""
    result = await client.call_tool(tool, args)
    assert result.is_error, f"expected an error, got {result.content}"
    return result.content[0].text  # type: ignore[union-attr]
