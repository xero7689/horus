"""`.env` discovery must not depend on the process CWD.

horus is installed as a global CLI (`~/.local/bin/horus`), so a CWD-relative
`env_file=".env"` silently loses HORUS_SERPER_API_KEY as soon as it runs from
anywhere but the repo — the serper adapter then errors and callers fall back to
a weaker adapter without knowing why.
"""

from pathlib import Path

from horus.config import Settings, env_files


def test_env_files_are_home_then_cwd(monkeypatch) -> None:
    monkeypatch.delenv("HORUS_BASE_DIR", raising=False)
    assert env_files() == (Path.home() / ".horus" / ".env", Path(".env"))


def test_env_files_honor_base_dir_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HORUS_BASE_DIR", str(tmp_path))
    assert env_files()[0] == tmp_path / ".env"


def test_settings_env_file_is_not_cwd_relative_only() -> None:
    configured = Settings.model_config["env_file"]
    assert isinstance(configured, tuple), "a bare '.env' string is CWD-relative"
    assert Path(configured[0]).is_absolute()
    assert Path(configured[-1]) == Path(".env")


def test_reads_key_from_base_dir_env_file_when_cwd_has_none(monkeypatch, tmp_path: Path) -> None:
    home_env = tmp_path / "home.env"
    home_env.write_text("HORUS_SERPER_API_KEY=from-base-dir\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)

    settings = Settings(_env_file=(home_env, Path(".env")))
    assert settings.serper_api_key == "from-base-dir"


def test_cwd_env_file_wins_over_base_dir(monkeypatch, tmp_path: Path) -> None:
    """Repo-local .env keeps overriding the machine-wide one (dev workflow)."""
    home_env = tmp_path / "home.env"
    home_env.write_text("HORUS_SERPER_API_KEY=from-base-dir\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("HORUS_SERPER_API_KEY=from-repo\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)

    settings = Settings(_env_file=(home_env, Path(".env")))
    assert settings.serper_api_key == "from-repo"


def test_settings_resolves_env_file_at_init(monkeypatch, tmp_path: Path) -> None:
    """The real-world case: run from another project, still find the key."""
    base = tmp_path / "horus-home"
    base.mkdir()
    (base / ".env").write_text("HORUS_SERPER_API_KEY=from-base-dir\n", encoding="utf-8")
    other_project = tmp_path / "some-other-project"
    other_project.mkdir()
    monkeypatch.setenv("HORUS_BASE_DIR", str(base))
    monkeypatch.chdir(other_project)
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)

    assert Settings().serper_api_key == "from-base-dir"


def test_explicit_env_file_none_still_wins(monkeypatch, tmp_path: Path) -> None:
    base = tmp_path / "horus-home"
    base.mkdir()
    (base / ".env").write_text("HORUS_SERPER_API_KEY=from-base-dir\n", encoding="utf-8")
    monkeypatch.setenv("HORUS_BASE_DIR", str(base))
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)

    settings = Settings(_env_file=None)
    assert settings.serper_api_key is None
