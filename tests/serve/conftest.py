"""Shared fixtures for serve tests."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from horus.core.storage import HorusStorage
from horus.serve.app import create_app
from horus.serve.crawler_manager import CrawlerManager
from horus.serve.deps import get_manager, get_storage, init


@pytest.fixture
def storage() -> HorusStorage:
    s = HorusStorage(Path(":memory:"), check_same_thread=False)
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture
def mock_manager() -> MagicMock:
    return MagicMock(spec=CrawlerManager)


@pytest.fixture
def client(storage: HorusStorage, mock_manager: MagicMock) -> TestClient:
    app = create_app()
    templates_dir = Path(__file__).parent.parent.parent / "src" / "horus" / "serve" / "templates"
    init(MagicMock(resolved_db_path=Path(":memory:")), mock_manager, templates_dir)

    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_manager] = lambda: mock_manager

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()
