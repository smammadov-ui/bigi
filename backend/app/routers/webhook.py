"""POST /api/webhook/jira — accept a Jira webhook (JSON) or a text/plain body.

When ``JIRA_WEBHOOK_SECRET`` is configured the request must carry it via the
``X-Webhook-Secret`` header OR a ``?secret=`` query param; otherwise the request
is rejected with 401. When the secret is unset, no check is performed.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..bo_client import BOError
from ..config import get_settings
from ..db import get_db
from ..jira import description_from_webhook
from ..pipeline import run_pipeline
from ..schemas import BigiError

router = APIRouter()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BigiError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))
    except BOError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))


def _check_secret(request: Request) -> None:
    configured = get_settings().jira_webhook_secret
    if not configured:
        return
    provided = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret")
    if provided != configured:
        raise HTTPException(status_code=401, detail="invalid or missing webhook secret")


@router.post("/api/webhook/jira")
async def jira_webhook(request: Request, db: Session = Depends(get_db)):
    _check_secret(request)

    raw = await request.body()
    body_text = raw.decode("utf-8", errors="replace") if raw else ""
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        try:
            payload = json.loads(body_text) if body_text else {}
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON webhook body")
        raw_text = description_from_webhook(payload)
    else:
        # text/plain (or anything else) -> use the body verbatim as the ticket.
        raw_text = body_text.strip()

    if not raw_text:
        raise HTTPException(status_code=400, detail="empty issue description")

    return _guard(run_pipeline, db, raw_text)
