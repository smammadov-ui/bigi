"""Jira pull routes:
  POST /api/jira/fetch  — fetch an issue by key, then run the pipeline.
  GET  /api/jira/search — list issues for the given (or configured) JQL.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..bo_client import BOError
from ..db import get_db
from ..jira import fetch_comment_match_uuids, fetch_issue, search_issues
from ..pipeline import run_pipeline
from ..schemas import JiraFetchRequest, BigiError
from ..settings_store import jira_config

router = APIRouter()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BigiError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))
    except BOError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))


@router.post("/api/jira/fetch")
def jira_fetch(body: JiraFetchRequest, db: Session = Depends(get_db)):
    cfg = jira_config(db)
    issue = _guard(fetch_issue, cfg, body.issue_key)
    # Submitters post definitive/potential match UUIDs in COMMENTS when the
    # description lacks them; a failed comment read never blocks the run.
    comment_uuids = fetch_comment_match_uuids(cfg, body.issue_key)
    result = _guard(run_pipeline, db, issue["description"], None, comment_uuids)
    # description is included so the UI can re-run the pipeline with a chosen
    # company_uuid (candidate pick / manual entry) without re-pasting the text.
    return {
        **result,
        "jira": {
            "key": issue["key"],
            "summary": issue["summary"],
            "description": issue["description"],
        },
    }


@router.get("/api/jira/search")
def jira_search(jql: str | None = None, db: Session = Depends(get_db)):
    return {"issues": _guard(search_issues, jira_config(db), jql)}
