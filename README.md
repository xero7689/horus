# Horus 🦅

Universal browser crawler CLI with pluggable site adapters. Powered by Playwright, stores results in SQLite with full-text search.

Built for sites that are hard to access via traditional APIs — handles login flows, JavaScript rendering, and anti-bot measures.

## Features

- **Pluggable adapters** — add any site in 3 steps
- **Login state management** — manual login once, reuse browser state forever
- **Two crawl modes**:
  - *Response interception* — captures API/GraphQL responses (e.g. Threads)
  - *Page mode* — fetches full rendered HTML → converts to Markdown (any public site)
- **Incremental crawls** — automatically resumes from last fetched timestamp
- **Graceful Ctrl+C** — saves in-progress data before exiting
- **SQLite + FTS5** — full-text search with CJK (Chinese/Japanese/Korean) support
- **Export** — JSON, CSV, or Markdown

## Installation

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/xero7689/horus.git
cd horus
uv sync
uv run playwright install chromium
```

## Quick Start

### Threads (requires login)

```bash
# Login once — opens a browser window
horus login threads

# Crawl a user's posts (incremental)
horus crawl threads --user @username

# Crawl replies instead
horus crawl threads --user @username --mode replies

# Search stored content
horus search "keyword" --site threads

# Export
horus export --site threads --format json -o out.json
horus export --site threads --format csv -o out.csv
```

`--limit` is the total for the whole crawl, `0` = unlimited. The default is 50, so
scrolling crawls (Instagram, Threads, Twitter) now stop at about 50 items instead of
scrolling until `HORUS_MAX_PAGES`; pass `--limit 0` to crawl a full profile. The `serve`
web form also defaults to 50 and behaves the same.

### Generic Web Pages (no login required)

```bash
# Fetch a single page as Markdown
horus crawl web --url https://example.com

# Fetch and save .md files to a directory
horus crawl web --url https://news.ycombinator.com --output ./pages/

# Batch crawl from a URL list (one URL per line, # for comments)
horus crawl web --url-list urls.txt --output ./pages/

# View stored pages
horus pages --site web --limit 10

# Export all stored pages as .md files
horus export --site web --format markdown --output ./export/
```

### Other commands

```bash
horus list-sites          # List all available adapters
horus show --site threads # Display stored items
horus stats               # Crawl statistics
```

## Adding a New Site Adapter

1. Create `src/horus/adapters/mysite.py`:

```python
from horus.adapters.base import SiteAdapter
from horus.models import ScrapedItem

class MySiteAdapter(SiteAdapter):
    site_id = "mysite"
    display_name = "My Site"
    login_url = "https://mysite.com/login"
    requires_login = True

    def get_response_filter(self):
        return lambda url, body: "mysite.com/api" in url and "data" in body

    def parse_response(self, body):
        return [
            ScrapedItem(id=item["id"], site_id=self.site_id, ...)
            for item in body.get("data", [])
        ]

    def get_urls(self, **kwargs):
        user = kwargs.get("user") or raise ValueError("--user required")
        return [f"https://mysite.com/{user}"]
```

2. Register in `src/horus/adapters/__init__.py`:

```python
from horus.adapters.mysite import MySiteAdapter
register(MySiteAdapter)
```

3. Verify: `horus list-sites`

## Configuration

All settings are via environment variables (prefix: `HORUS_`):

| Variable | Default | Description |
|---|---|---|
| `HORUS_BASE_DIR` | `~/.horus` | Data root directory |
| `HORUS_DB_PATH` | `~/.horus/data.db` | SQLite database path |
| `HORUS_HEADLESS` | `true` | Run browser headless |
| `HORUS_SCROLL_DELAY_MIN` | `3.0` | Min scroll delay (seconds) |
| `HORUS_SCROLL_DELAY_MAX` | `8.0` | Max scroll delay (seconds) |
| `HORUS_REQUEST_JITTER` | `2.0` | Extra random delay per request |
| `HORUS_MAX_PAGES` | `50` | Max scroll pages per crawl |

## Development

```bash
uv run pytest tests/ -v       # Run all tests
uv run ruff check src/ tests/ # Lint
uv run mypy src/               # Type check
```

## Project Structure

```
src/horus/
├── cli.py          # CLI entry point (Click)
├── config.py       # Settings (pydantic-settings)
├── models.py       # ScrapedItem, ScrapedPage, CrawlResult
├── core/
│   ├── browser.py  # Playwright lifecycle + login state
│   ├── scraper.py  # Scroll + response intercept / page fetch
│   └── storage.py  # SQLite + FTS5
└── adapters/
    ├── base.py     # SiteAdapter ABC
    ├── threads.py  # Threads (Meta)
    └── web.py      # Generic web pages
```

## License

MIT
