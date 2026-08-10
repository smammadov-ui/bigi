"""Sanitized decision trace for one Jira ticket — run LOCALLY while the bigi
server is up:

    python3 scripts/case_debug.py FPOPCL-24636
    python3 scripts/case_debug.py FPOPCL-24636 --company <uuid>   # replay an operator pick
    python3 scripts/case_debug.py FPOPCL-24636 --no-match         # operator: none of these
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
    company = ""
    if "--company" in args:
        company = args[args.index("--company") + 1].strip()
    no_match = "--no-match" in args

    r = httpx.post(f"{host}/api/jira/fetch", json={"issue_key": issue}, timeout=180)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return 1
    d = r.json()

    if company or no_match:
        # Replay the operator's decision: re-run the pipeline on the fetched
        # description with the chosen company UUID (the UI's "Use") or with
        # the "none of these" declaration (forces NO_MATCH -> S4).
        description = (d.get("jira") or {}).get("description") or ""
        if not description:
            print("no description returned by /api/jira/fetch — cannot re-run with a pick")
            return 1
        payload = {"raw_text": description}
        if company:
            payload["company_uuid"] = company
        if no_match:
            payload["no_match"] = True
        r = httpx.post(f"{host}/api/declaration", json=payload, timeout=180)
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
