import os
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


def env_files() -> tuple[Path, Path]:
    """`.env` locations, lowest priority first.

    horus is normally installed as a global CLI, so a bare `".env"` resolves
    against whatever CWD the caller happens to be in — running it from another
    project silently loses HORUS_SERPER_API_KEY. Look in the base dir
    (`~/.horus/.env`, or `$HORUS_BASE_DIR/.env`) first, then let a repo-local
    `.env` override it so the dev workflow inside the repo is unchanged.

    `HORUS_BASE_DIR` is read from the process env directly: the file list has to
    exist before `Settings` can be constructed.
    """
    base_dir = os.environ.get("HORUS_BASE_DIR")
    base = Path(base_dir).expanduser() if base_dir else Path.home() / ".horus"
    return (base / ".env", Path(".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HORUS_",
        env_file=env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, **kwargs: Any) -> None:
        # Resolve the file list per instantiation, not once at import: a runtime
        # HORUS_BASE_DIR (and tests that patch it) must be honoured. An explicit
        # `_env_file=` — including `None` — still wins.
        kwargs.setdefault("_env_file", env_files())
        super().__init__(**kwargs)

    base_dir: Path = Path.home() / ".horus"
    db_path: Path | None = None

    # Serper.dev Google Search API (env-only; never via CLI flag)
    serper_api_key: str | None = None

    # Browser behaviour
    headless: bool = True
    scroll_delay_min: float = 3.0
    scroll_delay_max: float = 8.0
    request_jitter: float = 2.0
    max_pages: int = 50

    @property
    def states_dir(self) -> Path:
        return self.base_dir / "states"

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or (self.base_dir / "data.db")

    def state_path_for(self, site_id: str) -> Path:
        return self.states_dir / f"{site_id}.json"

    def ensure_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir.mkdir(parents=True, exist_ok=True)
