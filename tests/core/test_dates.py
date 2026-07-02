from datetime import UTC, datetime

import pytest

from horus.core.dates import parse_since


def test_parse_since_none_and_empty() -> None:
    assert parse_since(None) is None
    assert parse_since("") is None


def test_parse_since_date_and_iso() -> None:
    assert parse_since("2026-01-02") == datetime(2026, 1, 2, tzinfo=UTC)
    assert parse_since("2026-01-02T03:04:05") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_since_invalid_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        parse_since("not-a-date")
