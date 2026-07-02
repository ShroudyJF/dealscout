import pytest

from dealscout.config import SettingsError, load_settings


def test_load_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ITAD_API_KEY", "k1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")
    monkeypatch.setenv("DEALSCOUT_DB", "custom.db")
    s = load_settings()
    assert (s.itad_api_key, s.telegram_bot_token, s.telegram_chat_id) == ("k1", "t1", "c1")
    assert s.db_path == "custom.db"


def test_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")
    with pytest.raises(SettingsError, match="ITAD_API_KEY"):
        load_settings()
