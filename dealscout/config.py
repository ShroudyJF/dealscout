"""Environment-based settings. Copy .env.example to .env and fill in keys."""

import os

from dotenv import load_dotenv
from pydantic import BaseModel


class SettingsError(RuntimeError):
    pass


class Settings(BaseModel):
    itad_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str
    display_currency: str = "MYR"
    tz: str = "Asia/Kuala_Lumpur"
    run_hour: int = 9


_REQUIRED = {
    "itad_api_key": "ITAD_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
}


def load_settings() -> Settings:
    load_dotenv()
    values: dict[str, object] = {}
    for field, env in _REQUIRED.items():
        value = os.environ.get(env, "").strip()
        if not value:
            raise SettingsError(f"missing environment variable {env}, see .env.example")
        values[field] = value
    values["db_path"] = os.environ.get("DEALSCOUT_DB", "dealscout.db")
    values["display_currency"] = os.environ.get("DEALSCOUT_DISPLAY_CURRENCY", "MYR").strip() or "MYR"
    values["tz"] = os.environ.get("DEALSCOUT_TZ", "Asia/Kuala_Lumpur").strip() or "Asia/Kuala_Lumpur"
    run_hour_raw = os.environ.get("DEALSCOUT_RUN_HOUR", "9").strip() or "9"
    try:
        run_hour = int(run_hour_raw)
    except ValueError as exc:
        raise SettingsError(
            f"DEALSCOUT_RUN_HOUR must be an integer 0-23, got {run_hour_raw!r}"
        ) from exc
    if not 0 <= run_hour <= 23:
        raise SettingsError(f"DEALSCOUT_RUN_HOUR must be 0-23, got {run_hour}")
    values["run_hour"] = run_hour
    return Settings(**values)
