from horus.config import Settings


def test_serper_api_key_read_from_env(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "test-key-123")
    settings = Settings(_env_file=None)  # ignore .env, read only process env
    assert settings.serper_api_key == "test-key-123"


def test_serper_api_key_defaults_none(monkeypatch):
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.serper_api_key is None
