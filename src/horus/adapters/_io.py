"""Shared I/O helpers for adapters.

Plain utility functions — adapters opt in by importing; nothing here is part of
the SiteAdapter contract, so adapter independence is preserved.
"""

import hashlib
import sys


def read_stdin_lines(*, skip_comments: bool = False) -> list[str]:
    """Read non-empty stripped lines from a piped stdin.

    Returns [] when stdin is a TTY (interactive) or unreadable — callers fall
    through to their own "missing required option" error.
    """
    if not (hasattr(sys.stdin, "isatty") and not sys.stdin.isatty()):
        return []
    try:
        lines = [line.strip() for line in sys.stdin if line.strip()]
    except OSError:
        return []
    if skip_comments:
        lines = [ln for ln in lines if not ln.startswith("#")]
    return lines


def stable_item_id(url: str, query: str) -> str:
    """Stable ID: SHA-1 of (url, query) truncated to 16 hex chars."""
    digest = hashlib.sha1(f"{url}|{query}".encode()).hexdigest()  # nosec B324
    return digest[:16]
