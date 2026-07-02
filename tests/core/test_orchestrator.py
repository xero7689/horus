"""Smoke: orchestrator http-mode path with a stub adapter and NullReporter."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from horus.adapters.base import SiteAdapter
from horus.config import Settings
from horus.core.orchestrator import CrawlOrchestrator
from horus.core.storage import HorusStorage
from horus.models import ScrapedItem


class _HttpStub(SiteAdapter):
    site_id = "http_stub"
    display_name = "HTTP Stub"
    login_url = ""
    requires_login = False
    has_http_mode = True

    def get_urls(self, **kwargs: Any) -> list[str]:
        return []

    async def fetch_items(self, **kwargs: Any) -> list[ScrapedItem]:
        assert kwargs["limit"] == 7  # top-level limit forwarded
        return [
            ScrapedItem(
                id="X1",
                site_id="http_stub",
                url="https://e.com/1",
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            )
        ]


def test_http_mode_forwards_limit_and_persists(tmp_path: Any) -> None:
    storage = HorusStorage(tmp_path / "t.db")
    orch = CrawlOrchestrator(storage, Settings())
    outcome = asyncio.run(
        orch.run(
            _HttpStub(),
            [],
            site="http_stub",
            kwargs={},
            limit=7,
            since=None,
            state_path=None,
        )
    )
    assert (outcome.total_found, outcome.total_new) == (1, 1)
    assert storage.get_items(site_id="http_stub", limit=10)[0].id == "X1"
    storage.close()
