"""`horus crawl --limit` input validation."""

from click.testing import CliRunner

from horus.cli import main


def test_negative_limit_is_a_usage_error() -> None:
    result = CliRunner().invoke(main, ["crawl", "web", "--limit", "-1", "--url", "https://e.com"])
    assert result.exit_code == 2
    assert "--limit" in result.output
