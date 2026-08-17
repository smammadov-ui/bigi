"""Process-level configuration (env-backed, ``.env`` supported).

Two kinds of values live here:

* App plumbing: the SQLite DB URL.
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

    # --- credential fallbacks (Settings UI wins when set) -------------------
    # When BO_INTTOKEN itself is unset, the token is read from this file on
    # EVERY request (fresh reads — refreshing the file needs no restart). It is
    # the same file the finom-bo-local MCP server uses, so the token is
    # maintained in exactly one place.
    bo_inttoken_file: str = "~/.finom-bo/token"

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


def _token_from_file(path: str) -> str:
    """Best-effort token read (expanded path, stripped); '' when unavailable."""
    if not path:
        return ""
    try:
        from pathlib import Path

        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def env_fallbacks() -> dict[str, str]:
    """The credential fallback map, keyed like ``settings_store.DEFAULTS``.

    ``bo_inttoken`` resolution: env/.env value first, else the token FILE
    (read per call, so a refreshed file takes effect immediately).
    """
    s = get_settings()
    return {
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model,
        "llm_api_key": s.llm_api_key,
        "bo_base_url": s.bo_base_url,
        "bo_inttoken": s.bo_inttoken or _token_from_file(s.bo_inttoken_file),
        "jira_base_url": s.jira_base_url,
        "jira_email": s.jira_email,
        "jira_api_token": s.jira_api_token,
        "jira_jql": s.jira_jql,
    }
