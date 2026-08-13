"""Dump the RAW BO seizure listing for one company — statuses as the API
returns them, before any is_processing filtering. Diagnostic for cases where
case_debug shows processing_count=0 but the BO UI shows seizures (FPOPCL-31278).

Run LOCALLY from backend/ (same env as the bigi server):

    .venv/bin/python3 scripts/seizure_probe.py a6d90e77-c145-4f2c-9805-ed9ad313f58d

Prints, for the single-workspace view AND the widened (all-workspaces) view:
the row count, and per row: id, RAW status value (repr, so a dict-vs-string
shape difference is visible), normalized status_name, caseNumber, created.
Read-only throughout (the workspace widening is the same session preference
the pipeline itself uses, restored afterwards).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bo_client import BOClient, BOError, status_name  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.settings_store import bo_config  # noqa: E402
from app.workspaces import all_workspaces  # noqa: E402


def _dump(client: BOClient, company_uuid: str, label: str) -> None:
    print(f"--- {label} " + "-" * (60 - len(label)))
    try:
        listing = client.list_seizures(company_uuid)
    except BOError as exc:
        print(f"  list_seizures FAILED: {exc}")
        return
    rows = listing.get("seizures") or []
    print(f"  rows returned: {len(rows)}")
    for i, s in enumerate(rows, 1):
        raw_status = s.get("status")
        print(f"  [{i}] id={s.get('id')!r}")
        print(f"      status(raw)={raw_status!r}  status_name={status_name(raw_status)!r}")
        print(f"      caseNumber={s.get('caseNumber')!r}  created={s.get('created')!r}")
    if rows:
        print("  keys of first row: " + ", ".join(sorted(rows[0].keys())))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    company_uuid = sys.argv[1].strip()

    db = SessionLocal()
    try:
        bo = bo_config(db)
    finally:
        db.close()
    if not bo.get("base_url") or not bo.get("inttoken"):
        print("BO base_url/inttoken not configured (settings UI or backend/.env)")
        return 1
    client = BOClient(bo["base_url"], bo["inttoken"])

    try:
        profile = client.whoami() or {}
        print(f"contexts available={profile.get('contexts')!r} "
              f"active={profile.get('activeContexts')!r}")
    except BOError as exc:
        print(f"whoami failed: {exc}")

    # 1) exactly what the pipeline would see WITHOUT widening
    _dump(client, company_uuid, "single-workspace (current active contexts)")

    # 2) what the pipeline sees inside all_workspaces (its real code path)
    with all_workspaces(client) as ws:
        print(f"widening: switched={ws['switched']} error={ws['error']!r}")
        _dump(client, company_uuid, "all workspaces widened")
    if ws.get("restore_error"):
        print(f"RESTORE ERROR: {ws['restore_error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
