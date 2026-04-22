from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError
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
    bot_token: str = Field(..., alias="BOT_TOKEN")
    web_session_secret: str = Field(default="", alias="WEB_SESSION_SECRET")

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

    # Speech-to-text
    stt_api_key: str = Field(default="", alias="STT_API_KEY")
    stt_api_base_url: str = Field(
        default="https://api.together.ai/v1",
        alias="STT_API_BASE_URL",
    )
    stt_model: str = Field(default="openai/whisper-large-v3", alias="STT_MODEL")
    stt_language: str = Field(default="ru", alias="STT_LANGUAGE")

    # API
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # App
    app_name: str = Field(default="AI Telegram Digest Bot", alias="APP_NAME")
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_telegram_user_ids: str = Field(default="", alias="ADMIN_TELEGRAM_USER_IDS")

    # Database
    database_url: str = Field(
        default="sqlite:///./data/digest.db",
        alias="DATABASE_URL",
    )

    @property
    def admin_telegram_user_id_set(self) -> set[int]:
        user_ids: set[int] = set()
        for raw_value in self.admin_telegram_user_ids.split(","):
            value = raw_value.strip()
            if not value:
                continue
            try:
                user_ids.add(int(value))
            except ValueError:
                continue
        return user_ids


@lru_cache
def get_settings() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        bot_token_locs = {("bot_token",), ("BOT_TOKEN",)}
        missing_bot_token = any(
            tuple(error.get("loc", ())) in bot_token_locs
            and error.get("type") == "missing"
            for error in exc.errors()
        )
        if missing_bot_token:
            raise RuntimeError(
                "Configuration error: BOT_TOKEN is missing. Set BOT_TOKEN in .env."
            ) from exc
        raise RuntimeError("Configuration error: invalid values in .env.") from exc

    if not settings.bot_token.strip():
        raise RuntimeError(
            "Configuration error: BOT_TOKEN is empty. Set BOT_TOKEN in .env."
        )

    return settings
