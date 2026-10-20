import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from horus.adapters import get_adapter, list_adapters
from horus.config import Settings
from horus.core.browser import BaseBrowser
from horus.core.crawl_plan import resolve_crawl_plan
from horus.core.dates import parse_since
from horus.core.orchestrator import CrawlOrchestrator, CrawlOutcome
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage
from horus.core.thread_tree import build_thread_trees, render_thread_md
from horus.models import ScrapedItem, ScrapedPage

console = Console(stderr=True)


def _get_settings() -> Settings:
    return Settings()


def _get_storage(settings: Settings | None = None) -> HorusStorage:
    s = settings or _get_settings()
    s.ensure_dirs()
    return HorusStorage(s.resolved_db_path)


def _parse_since(since_str: str | None) -> datetime | None:
    try:
        return parse_since(since_str)
    except ValueError:
        raise click.BadParameter(f"Invalid date format: '{since_str}'. Use YYYY-MM-DD or ISO 8601.")


def _parse_extra_args(extra_args: tuple[str, ...]) -> dict[str, str]:
    """Parse --key value pairs from extra_args tuple."""
    result: dict[str, str] = {}
    it = iter(extra_args)
    for token in it:
        if token.startswith("--"):
            key = token.lstrip("-").replace("-", "_")
            try:
                value = next(it)
                if not value.startswith("--"):
                    result[key] = value
                else:
                    result[key] = "true"
            except StopIteration:
                result[key] = "true"
    return result


@click.group()
@click.version_option()
def main() -> None:
    """Horus — universal browser crawler with pluggable site adapters."""


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@main.command()
@click.argument("site")
@click.option("--output", "-o", default=None, help="Override output path for browser state")
def login(site: str, output: str | None) -> None:
    """Open browser for manual login, save auth state for future crawls.

    Example: horus login threads
    """
    asyncio.run(_login(site, output))


async def _login(site: str, output_override: str | None) -> None:
    settings = _get_settings()
    settings.ensure_dirs()

    try:
        adapter_cls = get_adapter(site)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    adapter = adapter_cls()
    output_path = Path(output_override) if output_override else settings.state_path_for(site)

    console.print(f"Opening browser for [bold]{adapter.display_name}[/bold]...")
    console.print(f"State will be saved to: [dim]{output_path}[/dim]")

    async with BaseBrowser(headless=False) as browser:
        await browser.save_login_state(
            adapter.login_url,
            output_path,
            prompt="Press Enter when you are done logging in...",
        )

    console.print(f"[green]Login state saved:[/green] {output_path}")


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------


@main.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.argument("site")
@click.option(
    "--since", "-s", default=None, help="Only fetch items newer than this date (YYYY-MM-DD)"
)  # noqa: E501
@click.option("--limit", "-n", default=50, type=int, help="Max items to fetch (0 = unlimited)")
@click.option("--no-state", is_flag=True, help="Ignore saved login state")
@click.option(
    "--output", "-o", default=None, help="Output directory for .md files (page-mode adapters only)"
)  # noqa: E501
@click.option("--stdout", is_flag=True, help="Print results as JSONL to stdout")
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def crawl(
    site: str,
    since: str | None,
    limit: int,
    no_state: bool,
    output: str | None,
    stdout: bool,
    extra_args: tuple[str, ...],
) -> None:  # noqa: E501
    """Crawl a site using its adapter.

    Pass adapter-specific options after the site name:

    \b
    horus crawl threads --user @someone
    horus crawl threads --user @someone --mode replies
    horus crawl web --url https://example.com --output ./pages/
    horus crawl ddg --query "python" --stdout | jq '.url'
    echo "https://example.com" | horus crawl web --stdout
    """
    asyncio.run(_crawl(site, since, limit, no_state, output, stdout, extra_args))


class _ConsoleReporter:
    """CrawlReporter that reproduces the CLI's Rich console + stdout JSONL output."""

    def __init__(self, emit_stdout: bool) -> None:
        self._emit_stdout = emit_stdout

    def log(self, message: str) -> None:
        console.print(message)

    def progress(self, scroll: int, total: int) -> None:
        console.print(f"  [dim]scroll #{scroll} — {total} items so far[/dim]")

    def emit(self, item: ScrapedItem | ScrapedPage) -> None:
        """Write one JSONL line to stdout if --stdout is set."""
        if self._emit_stdout:
            click.echo(item.model_dump_json())


async def _crawl(
    site: str,
    since_str: str | None,
    limit: int,
    no_state: bool,
    output: str | None,
    emit_stdout: bool,
    extra_args: tuple[str, ...],
) -> None:
    settings = _get_settings()
    storage = _get_storage(settings)

    kwargs = _parse_extra_args(extra_args)
    try:
        plan = resolve_crawl_plan(site, kwargs, settings, use_saved_state=not no_state)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        storage.close()
        sys.exit(1)

    if plan.login_state_missing:
        console.print(
            f"[yellow]Warning:[/yellow] No login state found at "
            f"{settings.state_path_for(site)}. Run [bold]horus login {site}[/bold] first."
        )

    output_dir = Path(output) if output else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    orch = CrawlOrchestrator(
        storage,
        settings,
        # Module-level name resolved at call time; patch("horus.cli.BaseScraper") still works.
        scraper_cls=BaseScraper,
        reporter=_ConsoleReporter(emit_stdout),
    )
    # Parse --since ONLY for the intercept path, exactly as before: _parse_since was
    # only reached inside the intercept branch, so `crawl web --since garbage` is
    # currently ignored — parsing eagerly for all modes would turn it into a
    # BadParameter failure (behavior change).
    since = (
        _parse_since(since_str)
        if not (plan.adapter_cls.has_http_mode or plan.adapter_cls.has_page_mode)
        else None
    )
    outcome = CrawlOutcome()
    try:
        await orch.run(
            plan.adapter,
            plan.urls,
            adapter_cls=plan.adapter_cls,
            site=site,
            kwargs=kwargs,
            limit=limit,
            since=since,
            state_path=plan.state_path,
            output_dir=output_dir,
            write_page_md=_write_page_md,
            outcome=outcome,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        outcome.interrupted = True
    except (ValueError, RuntimeError) as e:
        # Was the http-mode sys.exit path (known divergence #1: now wraps all modes).
        console.print(f"[red]{e}[/red]")
        storage.close()
        sys.exit(1)

    if outcome.interrupted:
        console.print(
            f"\n[yellow]Interrupted.[/yellow] "
            f"{outcome.total_found} fetched, {outcome.total_new} saved."
        )
    else:
        console.print(
            f"\n[bold]Done.[/bold] {outcome.total_found} fetched, {outcome.total_new} new saved."
        )
    storage.close()


def _write_page_md(page: ScrapedPage, output_dir: Path) -> None:
    """Write a ScrapedPage as a .md file, named by slugified URL."""
    import re

    slug = re.sub(r"[^\w\-]", "_", page.url.split("://", 1)[-1])
    slug = re.sub(r"_+", "_", slug).strip("_")[:200]
    filename = f"{slug}.md"
    header = f"# {page.title or page.url}\n\n> Source: {page.url}\n\n---\n\n"
    (output_dir / filename).write_text(header + page.markdown, encoding="utf-8")


# ---------------------------------------------------------------------------
# list-sites
# ---------------------------------------------------------------------------


@main.command("list-sites")
def list_sites() -> None:
    """List all available site adapters."""
    adapters = list_adapters()
    if not adapters:
        console.print("No adapters registered.")
        return

    table = Table(title="Available Site Adapters")
    table.add_column("Site ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Login Required", justify="center")
    table.add_column("Description", style="dim")

    for cls in sorted(adapters, key=lambda c: c.site_id):
        login_required = "[yellow]yes[/yellow]" if cls.requires_login else "[green]no[/green]"
        table.add_row(cls.site_id, cls.display_name, login_required, cls.description)

    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@main.command()
@click.option("--site", default=None, help="Filter by site ID")
@click.option("--author", "-a", default=None, help="Filter by author username")
@click.option("--since", "-s", default=None, help="Items after this date (YYYY-MM-DD)")
@click.option("--limit", "-n", default=20, type=int, help="Number of items to show")
def show(site: str | None, author: str | None, since: str | None, limit: int) -> None:
    """Display stored items."""
    storage = _get_storage()
    since_dt = _parse_since(since)
    items = storage.get_items(site_id=site, author_name=author, since=since_dt, limit=limit)
    storage.close()

    if not items:
        console.print("No items found.")
        return

    _print_items(items)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@main.command()
@click.argument("query")
@click.option("--site", default=None, help="Filter by site ID")
@click.option("--limit", "-n", default=20, type=int, help="Number of results")
def search(query: str, site: str | None, limit: int) -> None:
    """Search stored items by keyword (local FTS, no quota)."""
    storage = _get_storage()
    items = storage.search(query, site_id=site, limit=limit)
    storage.close()

    if not items:
        console.print(f"No results for '{query}'.")
        return

    console.print(f"Found [bold]{len(items)}[/bold] results for '{query}':\n")
    _print_items(items)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


@main.command()
@click.option("--site", default=None, help="Filter by site ID")
@click.option("--limit", "-n", default=20, type=int, help="Number of pages to show")
def pages(site: str | None, limit: int) -> None:
    """Display stored web pages (from page-mode adapters like 'web')."""
    storage = _get_storage()
    page_list = storage.get_pages(site_id=site, limit=limit)
    storage.close()

    if not page_list:
        console.print("No pages found.")
        return

    for pg in page_list:
        ts = pg.fetched_at.strftime("%Y-%m-%d %H:%M")
        console.print(f"[cyan]{pg.title or '(no title)'}[/cyan] [dim]{ts}[/dim]")
        console.print(f"  [dim]{pg.url}[/dim]")
        preview = pg.markdown[:150].replace("\n", " ")
        console.print(f"  {preview}...")
        console.print()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command()
@click.option("--site", default=None, help="Filter by site ID")
@click.option("--author", "-a", default=None, help="Filter by author")
@click.option("--url", default=None, help="Filter by exact URL (for page-mode exports)")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "csv", "markdown", "thread-md"]),
    default="json",
)
@click.option("--output", "-o", required=True, help="Output file path (or directory for markdown)")
@click.option("--limit", "-n", default=10000, type=int, help="Max items to export")
def export(
    site: str | None, author: str | None, url: str | None, fmt: str, output: str, limit: int
) -> None:  # noqa: E501
    """Export stored items to JSON, CSV, Markdown, or thread-md.

    For --format markdown, --output should be a directory; pages from
    page-mode adapters (e.g. 'web') are exported as .md files.
    For --format thread-md, --output should be a directory; each root
    post and its replies are rendered as a single threaded .md file.
    """
    storage = _get_storage()
    output_path = Path(output)

    if fmt == "markdown":
        page_list = storage.get_pages(site_id=site, url=url, limit=limit)
        storage.close()
        output_path.mkdir(parents=True, exist_ok=True)
        for pg in page_list:
            _write_page_md(pg, output_path)
        console.print(f"[green]Exported {len(page_list)} pages to {output_path}[/green]")
        return

    items = storage.get_items(site_id=site, author_name=author, limit=limit)
    storage.close()

    if fmt == "thread-md":
        output_path.mkdir(parents=True, exist_ok=True)
        count = _export_thread_md(items, output_path)
        console.print(f"[green]Exported {count} threads to {output_path}[/green]")
        return

    if fmt == "json":
        _export_json(items, output_path)
    else:
        _export_csv(items, output_path)

    console.print(f"[green]Exported {len(items)} items to {output_path}[/green]")


def _export_json(items: list[ScrapedItem], path: Path) -> None:
    data = [item.model_dump(mode="json") for item in items]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _export_thread_md(items: list[ScrapedItem], out_dir: Path) -> int:
    roots = build_thread_trees(items)
    written = 0
    skipped_orphans = 0
    for root in roots:
        if not root.is_root:
            skipped_orphans += 1
            continue
        author = root.item.author_name or root.item.author_id or "unknown"
        safe_author = _slug_for_filename(author) or "unknown"
        safe_id = _slug_for_filename(root.item.id) or "post"
        filename = f"{safe_author}_{safe_id}.md"
        (out_dir / filename).write_text(render_thread_md(root), encoding="utf-8")
        written += 1
    if skipped_orphans:
        console.print(
            f"[yellow]Skipped {skipped_orphans} orphan reply group(s) "
            f"(root post not in result set; re-export without --author/--limit "
            f"filters to include them).[/yellow]"
        )
    return written


def _slug_for_filename(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:80]


def _export_csv(items: list[ScrapedItem], path: Path) -> None:
    if not items:
        path.write_text("")
        return
    fieldnames = ["id", "site_id", "url", "text", "author_id", "author_name", "timestamp"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            row = item.model_dump(mode="json")
            row["timestamp"] = item.timestamp.isoformat()
            writer.writerow(row)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@main.command()
@click.option("--site", default=None, help="Filter by site ID")
def stats(site: str | None) -> None:
    """Show crawl statistics."""
    storage = _get_storage()
    data = storage.get_stats(site_id=site)
    storage.close()

    table = Table(title="Horus Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold green", justify="right")

    table.add_row("Total items", str(data["total"]))

    by_site: dict[str, int] = data.get("by_site", {})
    latest: dict[str, str | None] = data.get("latest_by_site", {})

    for sid, count in sorted(by_site.items()):
        table.add_row(f"  [{sid}] items", str(count))
        last = latest.get(sid)
        if last:
            table.add_row(f"  [{sid}] latest", last[:19])

    console.print(table)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
@click.option("--port", default=8000, show_default=True, type=int, help="Bind port")
def serve(host: str, port: int) -> None:
    """Start the Horus web UI at http://<host>:<port>."""
    import uvicorn

    console.print(f"Starting Horus web UI at [bold]http://{host}:{port}[/bold]")
    uvicorn.run("horus.serve.app:app", host=host, port=port)


# ---------------------------------------------------------------------------
# display helpers
# ---------------------------------------------------------------------------


def _print_items(items: list[ScrapedItem]) -> None:
    for item in items:
        ts = item.timestamp.strftime("%Y-%m-%d %H:%M")
        site_tag = f"[[dim]{item.site_id}[/dim]]"
        console.print(f"[cyan]@{item.author_name}[/cyan] [dim]{ts}[/dim] {site_tag}")
        if item.text:
            console.print(f"  {item.text[:200]}")
        console.print(f"  [dim]{item.url}[/dim]")

        extra = item.extra
        if extra.get("like_count") is not None or extra.get("reply_count") is not None:
            console.print(
                f"  [red]♥ {extra.get('like_count', 0)}[/red]  "
                f"[blue]↩ {extra.get('reply_count', 0)}[/blue]  "
                f"[green]🔁 {extra.get('repost_count', 0)}[/green]"
            )
        console.print()
