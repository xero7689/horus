"""FastAPI dependency providers for horus serve."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.templating import Jinja2Templates

from horus.core.storage import HorusStorage

if TYPE_CHECKING:
    from horus.serve.crawler_manager import CrawlerManager

# Set by app.py lifespan
_settings = None
_templates: Jinja2Templates | None = None
_manager: CrawlerManager | None = None


def init(settings, manager: CrawlerManager, templates_dir: Path) -> None:  # type: ignore[no-untyped-def]
    global _settings, _manager, _templates
    _settings = settings
    _manager = manager
    _templates = Jinja2Templates(directory=str(templates_dir))


def get_storage() -> HorusStorage:
    assert _settings is not None
    return HorusStorage(_settings.resolved_db_path, check_same_thread=False)


def get_templates() -> Jinja2Templates:
    assert _templates is not None
    return _templates


def get_manager() -> CrawlerManager:
    assert _manager is not None
    return _manager
