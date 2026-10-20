"""Tests for shared adapter I/O helpers."""

import io
from typing import Any

from horus.adapters._io import read_stdin_lines, stable_item_id


class _FakeStdin(io.StringIO):
    def isatty(self) -> bool:  # StringIO.isatty() returns False already; be explicit
        return False


def test_read_stdin_lines_basic(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("  a \n\nb\n"))
    assert read_stdin_lines() == ["a", "b"]


def test_read_stdin_lines_skip_comments(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("a\n# comment\nb\n"))
    assert read_stdin_lines(skip_comments=True) == ["a", "b"]
    # without the flag, comment lines pass through (the @user family keeps them)
    monkeypatch.setattr("sys.stdin", _FakeStdin("a\n# comment\n"))
    assert read_stdin_lines() == ["a", "# comment"]


def test_read_stdin_lines_tty_returns_empty(monkeypatch: Any) -> None:
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _Tty("ignored\n"))
    assert read_stdin_lines() == []


def test_read_stdin_lines_oserror_returns_empty(monkeypatch: Any) -> None:
    class _Broken:
        def isatty(self) -> bool:
            return False

        def __iter__(self) -> Any:
            raise OSError("closed")

    monkeypatch.setattr("sys.stdin", _Broken())
    assert read_stdin_lines() == []


def test_stable_item_id_is_deterministic_16_hex() -> None:
    a = stable_item_id("https://x.com/1", "query")
    assert a == stable_item_id("https://x.com/1", "query")
    assert len(a) == 16
    assert a != stable_item_id("https://x.com/1", "other")
