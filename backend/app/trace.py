"""Sanitized decision trace for a pipeline result (shared by the case_debug
script and the finom-bo MCP server's case_trace tool)."""
from __future__ import annotations


def build_trace(d: dict, include_document: bool = False) -> dict:
    """Condense a ``run_pipeline`` result into the decision trace.

    Sanitized by default — enums, counts, booleans, and reason strings. With
    ``include_document`` the composed subject/text are included (needed to
    verify the German output against the ops guide).
    """
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
                 "notes": plan.get("notes")} if plan else None,
        "declaration": None,
        "account": {
            "resolved": bool(acc.get("company_uuid")),
            "company_uuid": acc.get("company_uuid"),
            "business_name": acc.get("business_name"),
            "identified_by": acc.get("identified_by"),
            "outcome": acc.get("outcome"),
            "matched_by": acc.get("matched_by"),
            "needs_selection": acc.get("needs_selection"),
            "candidates": [
                {"id": c.get("id"), "businessName": c.get("businessName")}
                for c in (acc.get("candidates") or [])
            ],
            "account_type": acc.get("account_type"),
            "account_status": acc.get("account_status"),
            "status_bucket": acc.get("status_bucket"),
            "account_status_updated": acc.get("account_status_updated"),
            "has_address": bool(acc.get("account_address")),
            "has_dob": bool(acc.get("dob")),
            "wallet_iban_count": len(acc.get("ibans") or []),
            "seized_iban_source": acc.get("seized_iban_source"),
            "address_check": acc.get("address_check"),
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
        "balance": {"available_eur": bal.get("available_eur"),
                    "held_eur": bal.get("held_eur"),
                    "non_eur_wallets": len(bal.get("non_eur") or []),
                    "error": bal.get("error")} if bal else None,
        "amount": d.get("amount"),
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
    if decl:
        trace["declaration"] = {
            "template": decl.get("template"),
            "kind": decl.get("kind"),
            "composed_by": decl.get("composed_by"),
        }
        if include_document:
            trace["declaration"]["subject"] = decl.get("subject")
            trace["declaration"]["text"] = decl.get("text")
    return trace
