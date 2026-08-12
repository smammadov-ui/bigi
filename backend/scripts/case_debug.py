"""Sanitized decision trace for one Jira ticket — run LOCALLY while the bigi
server is up:

    python3 scripts/case_debug.py FPOPCL-24636
    python3 scripts/case_debug.py FPOPCL-24636 --company <uuid>   # replay an operator pick
    python3 scripts/case_debug.py FPOPCL-24636 --no-match         # operator: none of these
    python3 scripts/case_debug.py FPOPCL-24636 --host http://localhost:8000

Manual-mode replay (recompose from the run's decision set, no BO re-fetch):

    python3 scripts/case_debug.py FPOPCL-24636 --template T2      # operator picks the template
    python3 scripts/case_debug.py FPOPCL-24636 --role 9=report --role 12=ignore
    python3 scripts/case_debug.py FPOPCL-24636 --seizable 138.03

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
    template = ""
    if "--template" in args:
        template = args[args.index("--template") + 1].strip()
    roles: dict[str, str] = {}
    for i, a in enumerate(args):
        if a == "--role" and i + 1 < len(args) and "=" in args[i + 1]:
            rid, _, role = args[i + 1].partition("=")
            roles[rid.strip()] = role.strip()
    seizable = None
    if "--seizable" in args:
        seizable = args[args.index("--seizable") + 1].strip()

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

    if template or roles or seizable is not None:
        # Manual-mode replay: edit the run's decision set and recompose (pure —
        # no BO re-fetch). Prints a sanitized summary of the recompose.
        manual = d.get("manual") or {}
        decisions = dict(manual.get("decisions") or {})
        if not decisions:
            print(json.dumps(trace, indent=2, ensure_ascii=False))
            print("no manual block on the result — cannot recompose")
            return 1
        if template:
            decisions["template"] = template
        if roles:
            decisions["seizures"] = [
                {**row, "role": roles.get(str(row.get("id")), row.get("role"))}
                for row in decisions.get("seizures") or []
            ]
        if seizable is not None:
            decisions["seizable_eur"] = seizable
        r = httpx.post(f"{host}/api/declaration/compose",
                       json={"decisions": decisions,
                             "context": {**(manual.get("context") or {}),
                                         "options": manual.get("options") or {}},
                             "auto": manual.get("auto")},
                       timeout=180)
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text[:300]}")
            return 1
        c = r.json()
        trace["manual_recompose"] = {
            "template": (c.get("declaration") or {}).get("template"),
            "kind": (c.get("declaration") or {}).get("kind"),
            "composed_by": (c.get("declaration") or {}).get("composed_by"),
            "manual_template": c.get("manual_template"),
            "warnings": c.get("warnings"),
            "roles": {str(row.get("id")): row.get("role")
                      for row in decisions.get("seizures") or []},
        }

    print(json.dumps(trace, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
