"""Process-level configuration (env-backed).

Only two values live here: the SQLite DB URL and an optional Jira webhook
secret. All credentials (LLM / BO / Jira) are stored in the DB via the
Settings UI, NOT in env.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bigi_db: str = "sqlite:///./bigi.db"
    jira_webhook_secret: str = ""  # optional; when set, webhook requires it

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
