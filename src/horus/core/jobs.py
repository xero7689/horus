"""UI-agnostic background crawl jobs, shared by serve (SSE) and mcp.

Holds lifecycle mechanics only. Frontend policy — SSE fan-out, one crawl per
site, error formatting — stays in the frontend.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from rich.text import Text

from horus.core.orchestrator import CrawlOutcome


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})


def strip_markup(message: str) -> str:
    """Rich markup → plain text for job logs.

    Messages interpolate unsanitized content (URLs, exception text): a stray
    "[/...]" raises MarkupError, so fall back to the raw string rather than let
    a log line kill the crawl.
    """
    try:
        return Text.from_markup(message).plain
    except Exception:
        return message


@dataclass
class CrawlJob:
    job_id: str
    site: str
    kwargs: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    # Passed into CrawlOrchestrator.run(outcome=...) so counts are live.
    outcome: CrawlOutcome = field(default_factory=CrawlOutcome)
    refs: list[str] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    log: list[str] = field(default_factory=list)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def items_found(self) -> int:
        return self.outcome.total_found

    @property
    def items_new(self) -> int:
        return self.outcome.total_new

    @property
    def finished(self) -> bool:
        return self.status in _TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "site": self.site,
            "kwargs": self.kwargs,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "items_found": self.items_found,
            "items_new": self.items_new,
            "error": self.error,
            "log": self.log[-20:],  # last 20 log lines
        }
