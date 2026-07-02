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


_REQUIRED = {
    "itad_api_key": "ITAD_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
}


def load_settings() -> Settings:
    load_dotenv()
    values: dict[str, str] = {}
    for field, env in _REQUIRED.items():
        value = os.environ.get(env, "").strip()
        if not value:
            raise SettingsError(f"missing environment variable {env}, see .env.example")
        values[field] = value
    values["db_path"] = os.environ.get("DEALSCOUT_DB", "dealscout.db")
    return Settings(**values)
