from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram bot
    bot_token: str = Field(default="", alias="BOT_TOKEN")

    # Telegram client for Telethon
    telegram_api_id: str = Field(default="", alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    telegram_phone: str = Field(default="", alias="TELEGRAM_PHONE")

    # Together AI
    together_api_key: str = Field(default="", alias="TOGETHER_API_KEY")
    together_model: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct-Turbo",
        alias="TOGETHER_MODEL",
    )

    # API
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # App
    app_name: str = Field(default="AI Telegram Digest Bot", alias="APP_NAME")
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Database
    database_url: str = Field(
        default="sqlite:///./data/digest.db",
        alias="DATABASE_URL",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()