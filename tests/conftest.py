from pathlib import Path

import pytest

from horus.core.storage import HorusStorage


@pytest.fixture
def storage() -> HorusStorage:
    """In-memory SQLite storage for tests."""
    s = HorusStorage(Path(":memory:"))
    yield s
    s.close()
