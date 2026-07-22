import pytest

from dealscout.config import SettingsError, load_settings


def test_load_settings_from_env(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("DEALSCOUT_DB", "custom.db")
    s = load_settings()
    assert (s.itad_api_key, s.telegram_bot_token, s.telegram_chat_id) == ("k1", "t1", "c1")
    assert s.db_path == "custom.db"


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c1")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    with pytest.raises(SettingsError, match="ITAD_API_KEY"):
        load_settings()


def test_locale_defaults(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    for var in ("DEALSCOUT_DISPLAY_CURRENCY", "DEALSCOUT_TZ", "DEALSCOUT_RUN_HOUR"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.display_currency == "MYR"
    assert s.tz == "Asia/Kuala_Lumpur"
    assert s.run_hour == 9


def test_locale_overrides(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("DEALSCOUT_DISPLAY_CURRENCY", "SGD")
    monkeypatch.setenv("DEALSCOUT_TZ", "America/New_York")
    monkeypatch.setenv("DEALSCOUT_RUN_HOUR", "7")
    s = load_settings()
    assert (s.display_currency, s.tz, s.run_hour) == ("SGD", "America/New_York", 7)


def test_invalid_run_hour_raises(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("DEALSCOUT_RUN_HOUR", "not-a-number")
    with pytest.raises(SettingsError, match="DEALSCOUT_RUN_HOUR"):
        load_settings()


def test_gemini_settings(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("DEALSCOUT_LLM_MODEL", raising=False)
    s = load_settings()
    assert s.gemini_api_key == "g"
    assert s.llm_model == "gemini-2.5-flash"


def test_missing_gemini_key_raises(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SettingsError, match="GEMINI_API_KEY"):
        load_settings()


def test_llm_model_override(monkeypatch):
    monkeypatch.setattr("dealscout.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("ITAD_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("DEALSCOUT_LLM_MODEL", "gemini-3.0-pro")
    assert load_settings().llm_model == "gemini-3.0-pro"
