"""POST /api/declaration — run the pipeline on pasted ticket text."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..bo_client import BOError
from ..db import get_db
from ..pipeline import run_pipeline
from ..schemas import DeclarationRequest, BigiError

router = APIRouter()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BigiError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))
    except BOError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))


@router.post("/api/declaration")
def create_declaration(body: DeclarationRequest, db: Session = Depends(get_db)):
    if not body.raw_text or not body.raw_text.strip():
        raise HTTPException(status_code=400, detail="raw_text is required")
    return _guard(run_pipeline, db, body.raw_text, body.company_uuid)
