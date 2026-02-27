import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from horus.adapters import get_adapter, list_adapters
from horus.config import Settings
from horus.core.browser import BaseBrowser
from horus.core.scraper import BaseScraper
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem, ScrapedPage

console = Console()


def _get_settings() -> Settings:
    return Settings()


def _get_storage(settings: Settings | None = None) -> HorusStorage:
    s = settings or _get_settings()
    s.ensure_dirs()
    return HorusStorage(s.resolved_db_path)


def _parse_since(since_str: str | None) -> datetime | None:
    if not since_str:
        return None
    try:
        return datetime.fromisoformat(since_str).replace(tzinfo=UTC)
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
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def crawl(
    site: str,
    since: str | None,
    limit: int,
    no_state: bool,
    output: str | None,
    extra_args: tuple[str, ...],
) -> None:  # noqa: E501
    """Crawl a site using its adapter.

    Pass adapter-specific options after the site name:

    \b
    horus crawl threads --user @someone
    horus crawl threads --user @someone --mode replies
    horus crawl web --url https://example.com --output ./pages/
    """
    asyncio.run(_crawl(site, since, limit, no_state, output, extra_args))


async def _crawl(
    site: str,
    since_str: str | None,
    limit: int,
    no_state: bool,
    output: str | None,
    extra_args: tuple[str, ...],
) -> None:
    settings = _get_settings()
    storage = _get_storage(settings)

    try:
        adapter_cls = get_adapter(site)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        storage.close()
        sys.exit(1)

    adapter = adapter_cls()
    kwargs = _parse_extra_args(extra_args)

    try:
        urls = adapter.get_urls(**kwargs)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        storage.close()
        sys.exit(1)

    output_dir = Path(output) if output else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    state_path = None if no_state else settings.state_path_for(site)
    if state_path and not state_path.exists():
        if adapter.requires_login:
            console.print(
                f"[yellow]Warning:[/yellow] No login state found at {state_path}. "
                f"Run [bold]horus login {site}[/bold] first."
            )
        state_path = None

    started_at = datetime.now(UTC)
    total_found = 0
    total_new = 0

    scraper_kwargs: dict[str, Any] = {
        "headless": settings.headless,
        "scroll_delay_min": settings.scroll_delay_min,
        "scroll_delay_max": settings.scroll_delay_max,
        "request_jitter": settings.request_jitter,
        "max_pages": settings.max_pages,
    }

    interrupted = False
    try:
        if adapter_cls.has_http_mode:
            # Direct HTTP path (no Playwright): DDG etc.
            console.print(f"Searching [cyan]{site}[/cyan]...")
            items = await adapter.fetch_items(**kwargs)
            items = adapter.post_process(items)
            new_count = storage.upsert_items(items)
            total_found = len(items)
            total_new = new_count
            storage.log_crawl(site, str(kwargs), total_found, total_new, started_at)
        else:
            async with BaseScraper(**scraper_kwargs) as scraper:
                if adapter_cls.has_page_mode:
                    # Page-based path: HTML → Markdown
                    for url in urls:
                        console.print(f"Fetching [cyan]{url}[/cyan]...")
                        page = await scraper.scrape_page(url, state_path, site_id=site)
                        is_new = storage.upsert_page(page)
                        total_found += 1
                        total_new += int(is_new)
                        if output_dir:
                            _write_page_md(page, output_dir)
                            console.print(f"  [dim]→ saved markdown to {output_dir}[/dim]")
                    storage.log_crawl(
                        site,
                        urls[0] if len(urls) == 1 else f"{len(urls)} URLs",
                        total_found,
                        total_new,
                        started_at,
                    )  # noqa: E501
                else:
                    # Response-intercept path (Threads etc.)
                    since = _parse_since(since_str)
                    if since is None:
                        since = storage.get_latest_timestamp(site)

                    def on_progress(scroll: int, total: int) -> None:
                        console.print(f"  [dim]scroll #{scroll} — {total} items so far[/dim]")

                    def on_batch(batch: list[ScrapedItem]) -> None:
                        nonlocal total_found, total_new
                        batch = adapter.post_process(batch)
                        new_count = storage.upsert_items(batch)
                        total_found += len(batch)
                        total_new += new_count

                    for url in urls:
                        console.print(f"Crawling [cyan]{url}[/cyan]...")
                        await scraper.scrape(
                            url,
                            adapter.get_response_filter(),
                            adapter.parse_response,
                            state_path,
                            since=since,
                            on_progress=on_progress,
                            on_batch=on_batch,
                        )
                        storage.log_crawl(site, url, total_found, total_new, started_at)
    except (KeyboardInterrupt, asyncio.CancelledError):
        interrupted = True

    if interrupted:
        console.print(f"\n[yellow]Interrupted.[/yellow] {total_found} fetched, {total_new} saved.")
    else:
        console.print(f"\n[bold]Done.[/bold] {total_found} fetched, {total_new} new saved.")
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
@click.option("--format", "fmt", type=click.Choice(["json", "csv", "markdown"]), default="json")
@click.option("--output", "-o", required=True, help="Output file path (or directory for markdown)")
@click.option("--limit", "-n", default=10000, type=int, help="Max items to export")
def export(
    site: str | None, author: str | None, url: str | None, fmt: str, output: str, limit: int
) -> None:  # noqa: E501
    """Export stored items to JSON, CSV, or Markdown.

    For --format markdown, --output should be a directory.
    Pages from page-mode adapters (e.g. 'web') will be exported as .md files.
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

    if fmt == "json":
        _export_json(items, output_path)
    else:
        _export_csv(items, output_path)

    console.print(f"[green]Exported {len(items)} items to {output_path}[/green]")


def _export_json(items: list[ScrapedItem], path: Path) -> None:
    data = [item.model_dump(mode="json") for item in items]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


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
