"""CLI-layer tests for the serper adapter http-mode path.

Covers:
- `--limit` is forwarded into `fetch_items` (Part 1).
- adapter errors (e.g. missing API key) print a clean message and exit non-zero
  without a traceback (Part 3).
"""

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from horus.cli import main


def test_crawl_passes_limit_to_fetch_items(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    with patch(
        "horus.adapters.serper.SerperAdapter.fetch_items",
        new=AsyncMock(return_value=[]),
    ) as m:
        runner = CliRunner()
        result = runner.invoke(main, ["crawl", "serper", "--query", "x", "--limit", "30"])
        assert result.exit_code == 0, result.output
        assert m.call_args.kwargs.get("limit") == 30
        assert m.call_args.kwargs.get("query") == "x"


def test_crawl_missing_api_key_prints_clean_error(monkeypatch, tmp_path) -> None:
    """No HORUS_SERPER_API_KEY → clean error mentioning the env var, exit non-zero.

    Uses an isolated filesystem so the developer's real repo-root .env (which holds
    a working key) is not picked up by Settings() — otherwise the un-patched adapter
    would silently succeed instead of raising the missing-key ValueError.
    """
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["crawl", "serper", "--query", "x", "--limit", "10"])
    assert result.exit_code == 1, result.output
    assert "HORUS_SERPER_API_KEY" in result.output
    # No raw traceback leaked to the user.
    assert "Traceback" not in result.output
