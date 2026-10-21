"""MCP crawl tools: start (wait-then-background), status, cancel."""

from mcp.server.mcpserver import MCPServer

from horus.mcp.state import McpState


def build_adapter_kwargs(
    *,
    user: str | None,
    url: str | None,
    query: str | None,
    mode: str | None,
    with_comments: bool,
    gl: str | None,
    hl: str | None,
) -> dict[str, str]:
    """MCP params → adapter kwargs, shaped like the CLI's _parse_extra_args.

    Values are strings; None is omitted. with_comments must be the string "true"
    because the orchestrator checks kwargs.get("with_comments") == "true".
    """
    raw = {"user": user, "url": url, "query": query, "mode": mode, "gl": gl, "hl": hl}
    kwargs = {k: str(v) for k, v in raw.items() if v is not None}
    if with_comments:
        kwargs["with_comments"] = "true"
    return kwargs


def register(server: MCPServer, state: McpState) -> None:
    pass
