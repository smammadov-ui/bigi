"""DB-backed settings: the only persisted application state.

Stores credentials/config as plaintext key/value rows in ``app_settings``.
Secrets are NEVER returned in plaintext to the browser — ``public_view`` masks
them; ``get_all`` (and the typed getters) return real values for server-side
service use only.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AppSetting

# Known keys + their defaults when unset.
DEFAULTS: dict[str, str] = {
    "llm_provider": "openai",
    "llm_model": "",
    "llm_api_key": "",
    "bo_base_url": "",
    "bo_inttoken": "",
    "jira_base_url": "",
    "jira_email": "",
    "jira_api_token": "",
    "jira_jql": "project = FPOPCL ORDER BY created DESC",
}

KNOWN_KEYS: frozenset[str] = frozenset(DEFAULTS)
SECRET_KEYS: frozenset[str] = frozenset({"llm_api_key", "bo_inttoken", "jira_api_token"})
LLM_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic"})


def mask(secret: str) -> str:
    s = (secret or "").strip()
    return ("••••" + s[-4:]) if len(s) >= 4 else ("••••" if s else "")


def get(db: Session, key: str) -> str:
    """Return the stored value for ``key`` or its default."""
    row = db.get(AppSetting, key)
    if row is not None and row.value != "":
        return row.value
    return DEFAULTS.get(key, "")


def get_all(db: Session) -> dict[str, str]:
    """INTERNAL: every known key with real values (incl. secrets)."""
    stored = {row.key: row.value for row in db.scalars(select(AppSetting)).all()}
    out: dict[str, str] = {}
    for key, default in DEFAULTS.items():
        val = stored.get(key, "")
        out[key] = val if val != "" else default
    return out


def update(db: Session, patch: dict) -> None:
    """Upsert only known keys present in ``patch``.

    For a secret key, an empty string ("") CLEARS it; omitting the key leaves
    it unchanged. ``llm_provider`` is validated against the allowed set.
    """
    from .schemas import BigiError

    provider = patch.get("llm_provider")
    if provider is not None and provider not in LLM_PROVIDERS:
        raise BigiError(f"invalid llm_provider: {provider!r} (must be openai or anthropic)")

    changed = False
    for key, value in patch.items():
        if key not in KNOWN_KEYS:
            continue
        new_val = "" if value is None else str(value)
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=new_val))
        else:
            row.value = new_val
        changed = True
    if changed:
        db.commit()


def public_view(db: Session) -> dict:
    """Masked, browser-safe view of all settings (no plaintext secrets)."""
    v = get_all(db)
    return {
        "llm": {
            "provider": v["llm_provider"],
            "model": v["llm_model"],
            "api_key_masked": mask(v["llm_api_key"]),
            "api_key_set": bool(v["llm_api_key"]),
        },
        "bo": {
            "base_url": v["bo_base_url"],
            "inttoken_masked": mask(v["bo_inttoken"]),
            "inttoken_set": bool(v["bo_inttoken"]),
        },
        "jira": {
            "base_url": v["jira_base_url"],
            "email": v["jira_email"],
            "api_token_masked": mask(v["jira_api_token"]),
            "api_token_set": bool(v["jira_api_token"]),
            "jql": v["jira_jql"],
        },
    }


# --- Typed convenience getters (server-side; real secret values) ---------- #

def llm_config(db: Session) -> dict:
    v = get_all(db)
    return {
        "provider": v["llm_provider"],
        "model": v["llm_model"],
        "api_key": v["llm_api_key"],
    }


def bo_config(db: Session) -> dict:
    v = get_all(db)
    return {"base_url": v["bo_base_url"], "inttoken": v["bo_inttoken"]}


def jira_config(db: Session) -> dict:
    v = get_all(db)
    return {
        "base_url": v["jira_base_url"],
        "email": v["jira_email"],
        "api_token": v["jira_api_token"],
        "jql": v["jira_jql"],
    }
