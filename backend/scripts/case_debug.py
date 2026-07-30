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

import httpx


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

    acc = d.get("account") or {}
    parsed = d.get("parsed") or {}
    sc = d.get("seizure_check") or {}
    plan = d.get("plan") or {}
    bal = d.get("balance") or {}
    decl = d.get("declaration") or {}

    trace = {
        "status": d.get("status"),
        "scenario": d.get("scenario"),
        "plan": {"template": plan.get("template"), "action": plan.get("action"),
                 "notes": plan.get("notes")},
        "declaration": {"template": decl.get("template"), "kind": decl.get("kind"),
                        "composed_by": decl.get("composed_by")} if decl else None,
        "account": {
            "resolved": bool(acc.get("company_uuid")),
            "identified_by": acc.get("identified_by"),
            "outcome": acc.get("outcome"),
            "matched_by": acc.get("matched_by"),
            "needs_selection": acc.get("needs_selection"),
            "candidates": len(acc.get("candidates") or []),
            "account_type": acc.get("account_type"),
            "account_status": acc.get("account_status"),
            "status_bucket": acc.get("status_bucket"),
            "account_status_updated": acc.get("account_status_updated"),
            "has_address": bool(acc.get("account_address")),
            "has_dob": bool(acc.get("dob")),
            "wallet_iban_count": len(acc.get("ibans") or []),
            "seized_iban_source": acc.get("seized_iban_source"),
            "error": acc.get("error"),
            "reasons": acc.get("reasons"),
        } if acc else None,
        "alerts": d.get("alerts"),
        "seizures": {
            "processing_count": sc.get("processing_count"),
            "ignored_same_case": len(sc.get("ignored_same_case") or []),
            "ignored_later": len(sc.get("ignored_later") or []),
            "own_case_missing": sc.get("own_case_missing"),
            "assumed": sc.get("assumed"),
            "error": sc.get("error"),
        } if sc else None,
        "balance": {"known": bal.get("available_eur") is not None,
                    "non_eur_wallets": len(bal.get("non_eur") or []),
                    "error": bal.get("error")} if bal else None,
        "amount_source": (d.get("amount") or {}).get("source"),
        "parsed_fields_present": {
            k: bool(parsed.get(k))
            for k in ("company_uuid", "company_uuid_candidates", "seized_iban",
                      "debtor_ibans", "debtor_register_number", "debtor_name",
                      "debtor_address", "debtor_dob", "date_received",
                      "case_references", "seizure_amount")
        },
        "parsed_halted": parsed.get("halted"),
        "parser_warnings": parsed.get("warnings"),
        "pipeline_warnings": d.get("warnings"),
    }
    print(json.dumps(trace, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
