"""Settings router: read/write masked config + connection-test probes.

Test endpoints always return HTTP 200 with ``{"ok": bool, "detail": str}`` —
a failed probe is a test *result*, not an API error — and they never leak any
secret in the detail string.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import settings_store
from ..db import get_db
from ..schemas import BigiError, SettingsPatch

router = APIRouter()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BigiError as exc:
        raise HTTPException(status_code=getattr(exc, "code", 400), detail=str(exc))


def _flatten(patch: SettingsPatch) -> dict:
    """SettingsPatch -> flat key/value patch of known keys only.

    Secret fields left out (None) are NOT included (unchanged); an explicit ""
    is included (clears the secret).
    """
    out: dict[str, str] = {}
    llm = patch.llm or {}
    if "provider" in llm:
        out["llm_provider"] = llm["provider"]
    if "model" in llm:
        out["llm_model"] = llm["model"]
    if "api_key" in llm:
        out["llm_api_key"] = llm["api_key"]

    bo = patch.bo or {}
    if "base_url" in bo:
        out["bo_base_url"] = bo["base_url"]
    if "inttoken" in bo:
        out["bo_inttoken"] = bo["inttoken"]

    jira = patch.jira or {}
    if "base_url" in jira:
        out["jira_base_url"] = jira["base_url"]
    if "email" in jira:
        out["jira_email"] = jira["email"]
    if "api_token" in jira:
        out["jira_api_token"] = jira["api_token"]
    if "jql" in jira:
        out["jira_jql"] = jira["jql"]
    return out


@router.get("/api/settings")
def read_settings(db: Session = Depends(get_db)) -> dict:
    return settings_store.public_view(db)


@router.put("/api/settings")
def write_settings(patch: SettingsPatch, db: Session = Depends(get_db)) -> dict:
    flat = _flatten(patch)
    _guard(settings_store.update, db, flat)
    return settings_store.public_view(db)


@router.post("/api/settings/test/bo")
def test_bo(db: Session = Depends(get_db)) -> dict:
    from ..bo_client import BOClient, BOError

    cfg = settings_store.bo_config(db)
    if cfg.get("token_withheld"):
        return {"ok": False, "detail": cfg["token_withheld"]}
    if not cfg.get("base_url") or not cfg.get("inttoken"):
        return {"ok": False, "detail": "BO base URL / token not set"}
    try:
        client = BOClient(cfg["base_url"], cfg["inttoken"])
        client.cstools_search("ping")
        return {"ok": True, "detail": "reachable"}
    except BOError as exc:
        return {"ok": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — surface as a test result, never raise
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


@router.post("/api/settings/test/jira")
def test_jira(db: Session = Depends(get_db)) -> dict:
    from ..jira import search_issues

    cfg = settings_store.jira_config(db)
    if not cfg.get("base_url") or not cfg.get("email") or not cfg.get("api_token"):
        return {"ok": False, "detail": "Jira base URL / email / token not set"}
    try:
        search_issues(cfg, None)
        return {"ok": True, "detail": "reachable"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


@router.post("/api/settings/test/llm")
def test_llm(db: Session = Depends(get_db)) -> dict:
    from ..llm import compose

    cfg = settings_store.llm_config(db)
    if not cfg.get("api_key"):
        return {"ok": False, "detail": "LLM API key not set"}
    try:
        # Tiny bounded ping through the real composer. composed_by reflects
        # whether the provider actually answered.
        body = "§ 840 ZPO ping\nFinom Payments B.V."
        _text, composed_by = compose("T1", body, {}, [], cfg)
        if composed_by.startswith("llm:"):
            return {"ok": True, "detail": f"reachable ({composed_by})"}
        return {"ok": False, "detail": "provider unreachable (fell back to deterministic)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
