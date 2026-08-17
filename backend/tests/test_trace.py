"""Trace builder: full diagnostic vs share-safe redacted output."""
from __future__ import annotations

from app.trace import build_trace

RESULT = {
    "status": "ok",
    "scenario": "S2",
    "plan": {"template": "T2", "action": "letter", "notes": []},
    "account": {
        "company_uuid": "11111111-1111-1111-1111-111111111111",
        "business_name": "Secret Debtor GmbH",
        "outcome": "MATCH", "matched_by": "iban", "identified_by": "iban",
        "candidates": [{"id": "c1", "businessName": "Secret Debtor GmbH",
                        "regNumber": "HRB 42", "note": "n"}],
        "account_type": "Company", "status_bucket": "OPEN",
        "account_address": "Kapstadtring 7, 22297 Hamburg", "dob": "",
        "ibans": ["DE00"], "seized_iban_source": "main_wallet",
        "address_check": {"grade": "strong", "ticket": "kapstadtring 7 | 22297 hamburg",
                          "account": "kapstadtring 7 | 22297 hamburg"},
        "reasons": ["address check: strong — ticket 'kapstadtring 7' ~ account 'kapstadtring 7'"],
        "error": None,
    },
    "balance": {"available_eur": 8609.02, "held_eur": 82.41, "non_eur": [{}]},
    "amount": {"seized_eur": 138.03, "source": "own_case"},
    "seizure_check": {"processing_count": 1, "seizures": [{"caseNumber": "AZ-1"}]},
    "declaration": {"template": "T2", "kind": "letter", "composed_by": "deterministic",
                    "subject": "Drittschuldner…", "text": "…138,03 EUR…"},
    "parsed": {"debtor_name": "x", "warnings": ["operator-edited fields: debtor_name"]},
    "warnings": ["amount: captured 138,03 EUR"],
}


def test_full_trace_includes_diagnostics_but_no_iban_or_token():
    t = build_trace(RESULT)
    assert t["account"]["business_name"] == "Secret Debtor GmbH"
    assert t["account"]["reasons"]
    assert t["balance"]["available_eur"] == 8609.02
    assert t["account"]["address_check"]["ticket"]        # full address present
    # sanitized invariants: opaque uuid yes, but no full IBAN / token anywhere
    blob = str(t)
    assert "DE00" not in blob                              # wallet IBANs never traced
    assert t["account"]["wallet_iban_count"] == 1


def test_redacted_trace_drops_names_addresses_amounts():
    t = build_trace(RESULT, redact=True)
    a = t["account"]
    assert a["business_name"] is None
    assert a["reasons"] is None
    assert a["candidates"][0]["businessName"] is None
    assert a["candidates"][0]["id"] == "c1"               # opaque id kept
    assert a["address_check"] == {"grade": "strong"}      # only the grade
    assert t["balance"]["available_eur"] is None
    assert t["balance"]["non_eur_wallets"] == 1           # count kept
    assert t["amount"] is None
    assert t["parsed_fields_present"]["debtor_name"] is True   # boolean kept
    assert t["pipeline_warnings"] is None
    # enums/counts still there
    assert t["scenario"] == "S2" and t["seizures"]["processing_count"] == 1
    blob = str(t)
    assert "Secret Debtor" not in blob and "Kapstadtring" not in blob and "138" not in blob


def test_redact_suppresses_document_even_with_include_document():
    t = build_trace(RESULT, include_document=True, redact=True)
    assert "subject" not in t["declaration"] and "text" not in t["declaration"]
