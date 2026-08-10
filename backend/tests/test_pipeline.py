"""End-to-end pipeline tests (offline): result shape, statuses, and re-picks.

A StubBO is injected into ``app.pipeline`` so identify + checks run without any
network. With no LLM key the declaration is composed deterministically. The
scenario-by-scenario coverage lives in test_scenarios.py; this file covers the
result contract: exact keys, pending_selection, halted, and the candidate
re-pick flow.
"""
from __future__ import annotations

import pytest

from app import pipeline
from app.schemas import BigiError
from tests.fixtures import CASE_REF, IBAN, UUID, UUID2, company, fields, raw_ticket
from tests.stub_bo import StubBO

RESULT_KEYS = {
    "status", "parsed", "account", "alerts", "balance", "seizure_check",
    "scenario", "plan", "amount", "declaration", "warnings",
}


def _patch(monkeypatch, stub):
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    return stub


def test_result_shape_and_s1(monkeypatch, db, client):
    _patch(monkeypatch, StubBO(fixtures={UUID: company()}))
    r = pipeline.run_pipeline(db, raw_ticket())

    assert set(r.keys()) == RESULT_KEYS
    assert r["status"] == "ok"

    parsed = r["parsed"]
    for key in ("warnings", "halted", "halt_reasons"):
        assert key in parsed
    assert parsed["case_references"] == CASE_REF
    assert parsed["seizure_amount"] == "3000.00"

    account = r["account"]
    assert account["company_uuid"] == UUID
    assert account["identified_by"] == "ticket_uuid"
    assert account["outcome"] == "MATCH"
    assert account["status_bucket"] == "OPEN"
    assert account["needs_selection"] is False
    assert "wallets_items" not in account          # internal payload stripped

    assert r["alerts"] == {"open_rules": [], "open_count": 0, "total": 0,
                           "error": None, "assumed": False}
    assert r["scenario"] == "S1"
    assert r["plan"]["action"] == "letter"

    d = r["declaration"]
    assert d["template"] == "T1"
    assert d["composed_by"] == "deterministic"
    assert d["kind"] == "letter"
    assert "§ 840" in d["subject"]
    assert "Finom Payments B.V." in d["text"]
    assert "[" not in d["text"]

    bal = r["balance"]
    assert bal["available_eur"] == 5000.0
    assert bal["seizable_eur"] == 3000.0            # min(claim, available)
    assert bal["seizable_eur_de"] == "3.000,00"


def test_empty_raw_text_raises_400():
    with pytest.raises(BigiError):
        pipeline.run_pipeline(None, "   ")


def test_halted_parse_makes_no_bo_call(monkeypatch, db, client):
    stub = _patch(monkeypatch, StubBO(fixtures={UUID: company()}))
    # Masked IBAN halts the parser.
    r = pipeline.run_pipeline(db, "seizure amount: 100.00\nseized IBANs: DE12****3456\n")
    assert r["status"] == "halted"
    assert r["parsed"]["halted"] is True
    assert r["scenario"] is None and r["plan"] is None and r["declaration"] is None
    assert r["account"] is None
    assert stub.calls == []                          # BO untouched
    assert any("halted" in w for w in r["warnings"])


def test_needs_selection_returns_pending(monkeypatch, db, client):
    items = [
        {"id": UUID, "businessName": "ACME Trading GmbH", "regNumber": ""},
        {"id": UUID2, "businessName": "ACME Holding GmbH", "regNumber": ""},
    ]
    _patch(monkeypatch, StubBO(search_items_map={"ACME GmbH": items}))
    f = fields(company_uuid="", seized_iban="", debtor_register_number="")
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["status"] == "pending_selection"
    assert r["scenario"] is None and r["declaration"] is None
    assert r["account"]["needs_selection"] is True
    assert len(r["account"]["candidates"]) == 2
    assert r["alerts"] is None and r["balance"] is None and r["seizure_check"] is None


def test_repick_with_company_uuid_resolves(monkeypatch, db, client):
    items = [
        {"id": UUID, "businessName": "ACME Trading GmbH", "regNumber": ""},
        {"id": UUID2, "businessName": "ACME Holding GmbH", "regNumber": ""},
    ]
    stub = StubBO(fixtures={UUID: company()}, search_items_map={"ACME GmbH": items})
    _patch(monkeypatch, stub)
    f = fields(company_uuid="", seized_iban="", debtor_register_number="")
    # Operator picked the first candidate -> re-run with company_uuid.
    r = pipeline.run_pipeline(db, raw_ticket(f), company_uuid=UUID)
    assert r["status"] == "ok"
    assert r["account"]["identified_by"] == "manual"
    assert r["account"]["company_uuid"] == UUID
    assert r["scenario"] == "S1"
    assert r["declaration"]["template"] == "T1"


def test_identification_bo_failure_is_pending_with_error(monkeypatch, db, client):
    _patch(monkeypatch, StubBO(fail={"cstools_search"}))
    f = fields(company_uuid="")                      # forces a search
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["status"] == "pending_selection"
    assert r["account"]["needs_selection"] is True
    assert "cstools_search" in (r["account"]["error"] or "")
    assert r["declaration"] is None


def test_resolved_uuid_written_back_to_parsed(monkeypatch, db, client):
    _patch(monkeypatch, StubBO(fixtures={UUID: company()}, search_map={"HRB12345": UUID}))
    f = fields(company_uuid="")
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["parsed"]["company_uuid"] == UUID


def test_declaration_endpoint_e2e(monkeypatch, client):
    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    resp = client.post("/api/declaration", json={"raw_text": raw_ticket()})
    assert resp.status_code == 200
    body = resp.json()
    assert body["scenario"] == "S1"
    assert body["declaration"]["kind"] == "letter"


def test_declaration_endpoint_empty_400(client):
    resp = client.post("/api/declaration", json={"raw_text": "  "})
    assert resp.status_code == 400


# --- company UUIDs from Jira comments ----------------------------------------------


def test_single_comment_uuid_resolves_company(monkeypatch, db, client):
    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_name="Unknown Ltd")      # nothing findable by search
    r = pipeline.run_pipeline(db, raw_ticket(f), comment_uuids=[UUID])
    assert r["account"]["company_uuid"] == UUID
    assert r["account"]["identified_by"] == "ticket_uuid"
    assert r["scenario"] == "S1"
    assert any("Jira comment" in w for w in r["parsed"]["warnings"])


def test_conflicting_desc_and_comment_uuids_become_candidates(monkeypatch, db, client):
    fx1, fx2 = company(uuid=UUID), company(uuid=UUID2, name="Other GmbH", wallets=[])
    stub = StubBO(fixtures={UUID: fx1, UUID2: fx2})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    f = fields(seized_iban="", debtor_register_number="")   # desc uuid = UUID
    r = pipeline.run_pipeline(db, raw_ticket(f), comment_uuids=[UUID2])
    # Two distinct UUIDs -> candidates path; no ticket IBAN -> operator picks.
    assert r["status"] == "pending_selection"
    ids = [c["id"] for c in r["account"]["candidates"]]
    assert UUID in ids and UUID2 in ids


def test_comment_uuid_with_ticket_iban_disambiguates_by_wallet(monkeypatch, db, client):
    fx1 = company(uuid=UUID)                                # owns the seized IBAN
    fx2 = company(uuid=UUID2, name="Other GmbH",
                  wallets=[{"id": "w", "iban": "DE02120300000000202051",
                            "name": "Main", "balance": 1.0, "currency": "EUR"}])
    stub = StubBO(fixtures={UUID: fx1, UUID2: fx2})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    f = fields(company_uuid="", debtor_register_number="")  # seized IBAN present
    r = pipeline.run_pipeline(db, raw_ticket(f), comment_uuids=[UUID, UUID2])
    assert r["account"]["company_uuid"] == UUID
    assert r["account"]["identified_by"] == "wallet_iban"
