from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HORUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_dir: Path = Path.home() / ".horus"
    db_path: Path | None = None

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
