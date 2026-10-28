"""Tests for the crawl command's free-form `--key value` parsing."""

import asyncio
from typing import Any

from horus.cli import _parse_extra_args
from horus.config import Settings
from horus.core.orchestrator import CrawlOrchestrator
from horus.core.redaction import MASK
from horus.core.storage import HorusStorage
from tests.core.test_redaction import _HttpStub


def test_space_separated_value() -> None:
    assert _parse_extra_args(("--user", "@nasa", "--mode", "replies")) == {
        "user": "@nasa",
        "mode": "replies",
    }


def test_equals_separated_value() -> None:
    fake = "SECRET"
    assert _parse_extra_args(("--query=fastapi tutorial", f"--api-key={fake}")) == {
        "query": "fastapi tutorial",
        "api_key": fake,
    }


def test_equals_splits_only_on_the_first_sign() -> None:
    assert _parse_extra_args(("--url=https://e.com/?a=1",)) == {"url": "https://e.com/?a=1"}


def test_extra_leading_dashes_are_stripped() -> None:
    # An extra dash must not leak into the key (`_mode`); serve strips the same way.
    assert _parse_extra_args(("---mode=replies",)) == {"mode": "replies"}


def test_bare_flag_does_not_swallow_the_next_flag() -> None:
    assert _parse_extra_args(("--with-comments", "--user", "@nasa")) == {
        "with_comments": "true",
        "user": "@nasa",
    }


def test_trailing_bare_flag() -> None:
    assert _parse_extra_args(("--user", "@nasa", "--with-comments")) == {
        "user": "@nasa",
        "with_comments": "true",
    }


def test_equals_form_secret_never_reaches_crawl_log(tmp_path: Any) -> None:
    """`--api-key=SECRET` must be split, so masking sees `api_key` as the key."""
    _HttpStub.received_kwargs = {}
    storage = HorusStorage(tmp_path / "t.db")
    kwargs = _parse_extra_args(("--query", "x", "--api-key=sk-live-SECRET"))
    asyncio.run(
        CrawlOrchestrator(storage, Settings()).run(
            _HttpStub(),
            [],
            site="redact_stub",
            kwargs=kwargs,
            limit=10,
            since=None,
            state_path=None,
        )
    )

    logged = storage._conn.execute(
        "SELECT url FROM crawl_log WHERE site_id = ?", ("redact_stub",)
    ).fetchone()["url"]
    assert "sk-live-SECRET" not in logged
    assert MASK in logged
    assert _HttpStub.received_kwargs["api_key"] == "sk-live-SECRET"
    storage.close()
