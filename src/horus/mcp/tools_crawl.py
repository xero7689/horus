"""MCP crawl tools: start (wait-then-background), status, cancel."""

import asyncio
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from horus.adapters import get_adapter
from horus.core.crawl_plan import resolve_crawl_plan
from horus.core.dates import parse_since
from horus.core.jobs import JobStatus
from horus.mcp.formatting import crawl_params
from horus.mcp.state import McpState, format_job_error, job_payload

MAX_WAIT_SECONDS = 600


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


def _params_hint(site: str) -> str:
    try:
        names = [p["name"] for p in crawl_params(get_adapter(site)())]
    except ValueError:
        return ""
    return f" {site} accepts: {', '.join(names)}." if names else ""


def _unknown_job(job_id: str) -> ToolError:
    return ToolError(
        f"Unknown job_id {job_id!r}. Job records live in memory and are "
        "cleared when the MCP server restarts; stored items remain."
    )


def register(server: MCPServer, state: McpState) -> None:
    @server.tool()
    async def crawl(
        site: str,
        user: str | None = None,
        url: str | None = None,
        query: str | None = None,
        mode: str | None = None,
        limit: int = 10,
        since: str | None = None,
        with_comments: bool = False,
        gl: str | None = None,
        hl: str | None = None,
        wait_seconds: float = 45,
    ) -> dict[str, Any]:
        """Crawl a site into the local DB. Give one of user / url / query.

        Waits up to wait_seconds (0-600). Finished → status=done with counts and
        refs (item ids or page urls). Still running → status=running with job_id;
        poll crawl_status.

        limit caps items per profile/post (0 = unlimited; a stop threshold, the
        last batch is kept whole). Web pages are not limited by it. For serper,
        limit is the number of results requested (10-100); limit > 10 costs 2
        credits per query instead of 1.

        since (YYYY-MM-DD) applies to every scrolled URL (profiles, X statuses);
        threads / instagram single-post URLs ignore it. If omitted, each profile
        URL is incremental: only posts newer than the start of its last crawl
        that found items are fetched. A profile URL never crawled before (posts
        and replies tabs count separately) starts from its latest posts, and
        single-post / status URLs get no cutoff.

        Login is never done here: if a site needs it, run `horus login <site>`
        in a terminal.
        """
        if not (user or url or query):
            raise ToolError(f"crawl needs one of user / url / query.{_params_hint(site)}")
        if not 0 <= wait_seconds <= MAX_WAIT_SECONDS:
            raise ToolError(f"wait_seconds must be between 0 and {MAX_WAIT_SECONDS}.")
        if limit < 0:
            raise ToolError("limit must be >= 0 (0 = unlimited).")
        try:
            since_dt = parse_since(since) if since else None
        except ValueError as e:
            raise ToolError(f"Invalid since: {e}") from e
        busy = state.running_job_for(site)
        if busy is not None:
            raise ToolError(
                f"A {site} crawl is already running as job {busy.job_id}; "
                "poll crawl_status or crawl_cancel it first."
            )

        kwargs = build_adapter_kwargs(
            user=user,
            url=url,
            query=query,
            mode=mode,
            with_comments=with_comments,
            gl=gl,
            hl=hl,
        )
        try:
            plan = resolve_crawl_plan(site, kwargs, state.settings)
        except ValueError as e:
            # Adapter messages name CLI flags (--user); add the MCP param names.
            raise ToolError(f"{e}{_params_hint(site)}") from e
        except Exception as e:
            # Adapters can raise anything from get_urls (e.g. IsADirectoryError);
            # name the cause instead of the SDK's generic "Error executing tool".
            raise ToolError(f"{type(e).__name__}: {e}") from e
        if plan.login_state_missing:
            raise ToolError(
                f"{site} requires login and no saved login state was found. "
                f"Run `horus login {site}` in a terminal first."
            )

        job = state.jobs.submit(plan, kwargs, limit=limit, since=since_dt)
        assert job.task is not None
        # asyncio.wait, not wait_for: on timeout the crawl must keep running.
        await asyncio.wait({job.task}, timeout=wait_seconds)
        if job.status == JobStatus.FAILED:
            raise ToolError(
                f"{format_job_error(job) or 'crawl failed'} (job {job.job_id}, "
                f"{job.items_new} new items stored; see crawl_status)"
            )
        return job_payload(job)

    @server.tool()
    async def crawl_status(job_id: str) -> dict[str, Any]:
        """Status, live counts, refs, last 20 log lines and error of a crawl job."""
        job = state.jobs.get(job_id)
        if job is None:
            raise _unknown_job(job_id)
        return job_payload(job)

    @server.tool()
    async def crawl_cancel(job_id: str) -> dict[str, Any]:
        """Cancel a running crawl. Items already stored are kept.

        cancelled=false means the job had already finished."""
        if state.jobs.get(job_id) is None:
            raise _unknown_job(job_id)
        return {"job_id": job_id, "cancelled": state.jobs.cancel(job_id)}
