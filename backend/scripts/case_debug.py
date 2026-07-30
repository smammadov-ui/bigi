"""Sanitized decision trace for one Jira ticket — run LOCALLY while the bigi
server is up:

    python3 scripts/case_debug.py FPOPCL-24636
    python3 scripts/case_debug.py FPOPCL-24636 --host http://localhost:8000

Fetches the issue through the running backend (same path the UI uses) and
prints WHY the pipeline decided what it decided: scenario, plan notes, match
outcome/reasons, checks. Output is sanitized — booleans, enums, counts, and
reason strings only; no token, no names, no IBANs, no amounts — safe to share.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    issue = args[0]
    host = "http://localhost:8000"
    if "--host" in args:
        host = args[args.index("--host") + 1].rstrip("/")

    r = httpx.post(f"{host}/api/jira/fetch", json={"issue_key": issue}, timeout=180)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return 1
    d = r.json()

    from app.trace import build_trace

    trace = build_trace(d)
    print(json.dumps(trace, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
