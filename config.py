from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


def _default_group() -> str:
    return "15.14д-гг01/24м"


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True, env_prefix=""
    )

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    schedule_url: AnyHttpUrl = Field("https://rasp.rea.ru/", alias="SCHEDULE_URL")
    schedule_group: str = Field(default_factory=_default_group, alias="SCHEDULE_GROUP")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @field_validator("telegram_bot_token")
    def validate_token(cls, value: str) -> str:
        if not value or " " in value:
            raise ValueError("TELEGRAM_BOT_TOKEN must be a non-empty string without spaces")
        return value

    @field_validator("schedule_group")
    def validate_group(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("SCHEDULE_GROUP must not be empty")
        return cleaned


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()


__all__ = ["Settings", "get_settings"]
