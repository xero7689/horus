"""Date parsing shared by CLI and serve."""

from datetime import UTC, datetime


def parse_since(since_str: str | None) -> datetime | None:
    """Parse a --since string (YYYY-MM-DD or ISO 8601) to an aware UTC datetime.

    Returns None for None/empty. Raises ValueError on malformed input —
    callers decide the failure UX (CLI: BadParameter; serve: fall back to None).
    """
    if not since_str:
        return None
    return datetime.fromisoformat(since_str).replace(tzinfo=UTC)
