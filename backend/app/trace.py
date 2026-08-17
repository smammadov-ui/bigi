"""Decision trace for a pipeline result (shared by the case_debug script and
the finom-bo MCP server's case_trace tool).

TWO modes:

* Default (``redact=False``) — the full DIAGNOSTIC trace. It NEVER contains
  the INTTOKEN or any full IBAN, but it DOES contain the business name, the
  compared addresses, the reason strings (which quote names/addresses), and
  amounts. Treat it as INTERNAL debugging output — do not paste it into public
  channels.
* ``redact=True`` — a share-safe trace: business names, addresses, reason
  strings and amounts are dropped, leaving enums, counts, booleans, and the
  opaque company UUID. Use this when pasting into a ticket or chat.

``include_document`` additionally attaches the composed subject/text (needed to
verify the German output against the ops guide); it is ignored under
``redact``.
"""
from __future__ import annotations


def build_trace(d: dict, include_document: bool = False, redact: bool = False) -> dict:
    """Condense a ``run_pipeline`` result into the decision trace.

    See the module docstring for the ``redact`` / ``include_document`` modes.
    """
    acc = d.get("account") or {}
    parsed = d.get("parsed") or {}
    sc = d.get("seizure_check") or {}
    plan = d.get("plan") or {}
    bal = d.get("balance") or {}
    decl = d.get("declaration") or {}

    # Redacted mode hides free-text/amount PII; sanitized values stay.
    def keep(value):
        return None if redact else value

    trace = {
        "status": d.get("status"),
        "scenario": d.get("scenario"),
        "plan": {"template": plan.get("template"), "action": plan.get("action"),
                 "notes": plan.get("notes")} if plan else None,
        "declaration": None,
        "account": {
            "resolved": bool(acc.get("company_uuid")),
            "company_uuid": acc.get("company_uuid"),
            "business_name": keep(acc.get("business_name")),
            "identified_by": acc.get("identified_by"),
            "outcome": acc.get("outcome"),
            "matched_by": acc.get("matched_by"),
            "needs_selection": acc.get("needs_selection"),
            "candidates": [
                {"id": c.get("id"),
                 "businessName": keep(c.get("businessName")),
                 "regNumber": keep(c.get("regNumber")),
                 "note": c.get("note")}
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
            # address_check quotes the compared addresses -> only its grade
            # survives redaction.
            "address_check": (
                {"grade": (acc.get("address_check") or {}).get("grade")}
                if redact and acc.get("address_check")
                else acc.get("address_check")),
            "error": acc.get("error"),
            "reasons": keep(acc.get("reasons")),
        } if acc else None,
        "alerts": d.get("alerts"),
        "seizures": {
            "processing_count": sc.get("processing_count"),
            "competing_cases": [str(s.get("caseNumber") or s.get("id"))
                                for s in (sc.get("seizures") or [])],
            "ignored_same_case": len(sc.get("ignored_same_case") or []),
            "own_case_numbers": [str(s.get("caseNumber") or s.get("id"))
                                 for s in (sc.get("ignored_same_case") or [])],
            "ignored_later": len(sc.get("ignored_later") or []),
            "ignored_later_cases": [str(s.get("caseNumber") or s.get("id"))
                                    for s in (sc.get("ignored_later") or [])],
            "settling_count": len(sc.get("settling") or []),
            "settling_cases": [str(s.get("caseNumber") or s.get("id"))
                               for s in (sc.get("settling") or [])],
            "settling_captured_eur": keep(round(sum(
                float(s.get("seized_amount") or 0)
                for s in (sc.get("settling") or [])), 2)),
            "own_case_missing": sc.get("own_case_missing"),
            "assumed": sc.get("assumed"),
            "error": sc.get("error"),
        } if sc else None,
        "balance": {"available_eur": keep(bal.get("available_eur")),
                    "held_eur": keep(bal.get("held_eur")),
                    "non_eur_wallets": len(bal.get("non_eur") or []),
                    "error": bal.get("error")} if bal else None,
        "amount": keep(d.get("amount")),
        "parsed_fields_present": {
            k: bool(parsed.get(k))
            for k in ("company_uuid", "company_uuid_candidates", "seized_iban",
                      "debtor_ibans", "debtor_register_number", "debtor_name",
                      "debtor_address", "debtor_dob", "date_received",
                      "case_references", "seizure_amount")
        },
        "parsed_halted": parsed.get("halted"),
        # halt reasons can quote a raw invalid value from the ticket.
        "parser_warnings": keep(parsed.get("warnings")),
        "pipeline_warnings": keep(d.get("warnings")),
    }
    if decl:
        trace["declaration"] = {
            "template": decl.get("template"),
            "kind": decl.get("kind"),
            "composed_by": decl.get("composed_by"),
        }
        if include_document and not redact:
            trace["declaration"]["subject"] = decl.get("subject")
            trace["declaration"]["text"] = decl.get("text")
    return trace
