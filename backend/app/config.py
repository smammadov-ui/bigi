"""Process-level configuration (env-backed, ``.env`` supported).

Two kinds of values live here:

* App plumbing: the SQLite DB URL and the optional Jira webhook secret.
* **Credential fallbacks**: every Settings-UI credential can also be supplied
  via environment / a local ``.env`` file (``BO_BASE_URL``, ``BO_INTTOKEN``,
  ``LLM_API_KEY``, ``JIRA_API_TOKEN``, …). A value saved in the Settings UI
  (SQLite) takes precedence; the env value is used when the DB one is unset.
  This keeps tokens out of chat/git/UI: put them in ``backend/.env``
  (gitignored) or pass ``docker run -e BO_INTTOKEN=…``.

Secrets from env are treated exactly like DB secrets: masked toward the
browser, never logged.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bigi_db: str = "sqlite:///./bigi.db"
    jira_webhook_secret: str = ""  # optional; when set, webhook requires it

    # --- credential fallbacks (Settings UI wins when set) -------------------
    llm_provider: str = ""         # openai | anthropic
    llm_model: str = ""
    llm_api_key: str = ""
    bo_base_url: str = ""
    bo_inttoken: str = ""
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_jql: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def env_fallbacks() -> dict[str, str]:
    """The credential fallback map, keyed like ``settings_store.DEFAULTS``."""
    s = get_settings()
    return {
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model,
        "llm_api_key": s.llm_api_key,
        "bo_base_url": s.bo_base_url,
        "bo_inttoken": s.bo_inttoken,
        "jira_base_url": s.jira_base_url,
        "jira_email": s.jira_email,
        "jira_api_token": s.jira_api_token,
        "jira_jql": s.jira_jql,
    }
