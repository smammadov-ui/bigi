"""DB-backed settings with environment fallbacks.

Stores credentials/config as plaintext key/value rows in ``app_settings``.
Resolution order per key: **DB (Settings UI) -> environment / ``.env``
(``config.env_fallbacks``) -> default**. The env path keeps tokens out of the
browser and out of git — drop them in ``backend/.env`` or pass ``-e`` to
docker. Secrets are NEVER returned in plaintext to the browser —
``public_view`` masks them (and reports whether the effective value came from
the db or the env); ``get_all`` (and the typed getters) return real values for
server-side service use only.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import env_fallbacks
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


def source_of(db: Session, key: str) -> str:
    """Where the effective value comes from: "db" | "env" | "default"."""
    row = db.get(AppSetting, key)
    if row is not None and row.value != "":
        return "db"
    if env_fallbacks().get(key, "") != "":
        return "env"
    return "default"


def get_all(db: Session) -> dict[str, str]:
    """INTERNAL: every known key with real values (incl. secrets).

    Resolution per key: DB value -> env fallback -> default.
    """
    stored = {row.key: row.value for row in db.scalars(select(AppSetting)).all()}
    env = env_fallbacks()
    out: dict[str, str] = {}
    for key, default in DEFAULTS.items():
        val = stored.get(key, "")
        if val == "":
            val = env.get(key, "")
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
    """Masked, browser-safe view of all settings (no plaintext secrets).

    ``*_source`` reports where the effective secret comes from ("db" | "env" |
    "default") so the UI can show that a token was supplied via environment.
    """
    v = get_all(db)
    return {
        "llm": {
            "provider": v["llm_provider"],
            "model": v["llm_model"],
            "api_key_masked": mask(v["llm_api_key"]),
            "api_key_set": bool(v["llm_api_key"]),
            "api_key_source": source_of(db, "llm_api_key"),
        },
        "bo": {
            "base_url": v["bo_base_url"],
            "inttoken_masked": mask(v["bo_inttoken"]),
            "inttoken_set": bool(v["bo_inttoken"]),
            "inttoken_source": source_of(db, "bo_inttoken"),
            "base_url_source": source_of(db, "bo_base_url"),
        },
        "jira": {
            "base_url": v["jira_base_url"],
            "email": v["jira_email"],
            "api_token_masked": mask(v["jira_api_token"]),
            "api_token_set": bool(v["jira_api_token"]),
            "api_token_source": source_of(db, "jira_api_token"),
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


def _same_host(a: str, b: str) -> bool:
    from urllib.parse import urlsplit

    ha = urlsplit((a or "").strip()).netloc.lower()
    hb = urlsplit((b or "").strip()).netloc.lower()
    return bool(ha) and ha == hb


def bo_config(db: Session) -> dict:
    """Effective BO ``{base_url, inttoken}`` for server-side use.

    Security guard (audit B1): the shared INTTOKEN provisioned via env/.env or
    the token FILE is only attached when the effective ``base_url`` points at
    the SAME host as the env-configured ``BO_BASE_URL``. If someone redirects
    ``bo_base_url`` (e.g. through the settings API) to a different host, the
    token is withheld — so it can never be exfiltrated to an attacker's host.
    A UI-set token (source "db") is the operator's own choice and is not
    second-guessed; when no env ``BO_BASE_URL`` is configured there is nothing
    to validate against and the value is used as-is.
    """
    v = get_all(db)
    base = v["bo_base_url"]
    token = v["bo_inttoken"]
    out = {"base_url": base, "inttoken": token}
    if token and source_of(db, "bo_inttoken") == "env":
        env_base = env_fallbacks().get("bo_base_url", "")
        if env_base and not _same_host(base, env_base):
            out["inttoken"] = ""
            out["token_withheld"] = (
                "BO base_url host does not match the env-configured BO_BASE_URL "
                "— the shared token was withheld to prevent exfiltration")
    return out


def jira_config(db: Session) -> dict:
    v = get_all(db)
    return {
        "base_url": v["jira_base_url"],
        "email": v["jira_email"],
        "api_token": v["jira_api_token"],
        "jql": v["jira_jql"],
    }
