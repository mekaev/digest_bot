from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    app_name: str = Field(default='AI Telegram Digest Bot', validation_alias='APP_NAME')
    environment: str = Field(default='dev', validation_alias='ENVIRONMENT')
    log_level: str = Field(default='INFO', validation_alias='LOG_LEVEL')

    bot_token: str = Field(default='', validation_alias='BOT_TOKEN')

    together_api_key: str = Field(default='', validation_alias='TOGETHER_API_KEY')
    together_model: str = Field(
        default='meta-llama/Llama-3.3-70B-Instruct-Turbo',
        validation_alias='TOGETHER_MODEL',
    )

    database_url: str = Field(default='sqlite:///./data/digest.db', validation_alias='DATABASE_URL')

    api_host: str = Field(default='127.0.0.1', validation_alias='API_HOST')
    api_port: int = Field(default=8000, validation_alias='API_PORT')


@lru_cache
def get_settings() -> Settings:
    return Settings()
