"""Manual mode: decision-set building, pure recompose, validation warnings."""
from __future__ import annotations

import pytest

from app import pipeline
from app.decisions import (
    TEMPLATE_CATALOG,
    compose_from_decisions,
    validate_decisions,
)
from app.schemas import BigiError
from tests.fixtures import CASE_REF, IBAN, UUID, company, fields, raw_ticket
from tests.stub_bo import StubBO

OWN = {"id": 9, "status": "Processing", "caseNumber": CASE_REF,
       "created": "2026-02-01T10:00:00Z"}
OTHER = {"id": 12, "status": "Processing", "caseNumber": "77 K 111/25",
         "created": "2026-01-15T10:00:00Z"}


def run(monkeypatch, db, stub, f):
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    return pipeline.run_pipeline(db, raw_ticket(f))


# --- the manual block on pipeline results -------------------------------------


def test_manual_block_s2(monkeypatch, db, client):
    fx = company(
        seizures=[OWN, OTHER],
        details={9: {**OWN, "seizedAmount": 138.03},
                 12: {**OTHER, "seizedAmount": 500.0, "amount": 900.0,
                      "creditorName": "AOK PLUS"}})
    r = run(monkeypatch, db, StubBO(fixtures={UUID: fx}), fields())
    m = r["manual"]
    d = m["decisions"]
    assert d["template"] == r["plan"]["template"] == "T2"
    assert m["auto"] == {"scenario": "S2", "template": "T2"}
    roles = {row["id"]: row["role"] for row in d["seizures"]}
    assert roles == {12: "report", 9: "own"}
    assert all(row["auto_role"] == row["role"] for row in d["seizures"])
    assert d["own_case_amount"] == 138.03
    assert d["seized_iban"]["value"] == IBAN
    # Subject is NOT prefilled — it follows the template (see compose).
    assert d["subject"] == "" and d["subject_pinned"] is False
    assert m["options"]["status_bucket"] == "OPEN"
    assert any(w["iban"] == IBAN for w in m["options"]["wallets"])
    assert m["context"]["fields"]["case_references"] == CASE_REF
    assert "warnings" not in m["context"]["fields"]


def test_manual_block_present_on_pending_selection(monkeypatch, db, client):
    # Dead ends stay completable: the block exists with empty decisions.
    fx = company()
    stub = StubBO(fixtures={UUID: fx},
                  search_items_map={"ACME GmbH": [
                      {"id": UUID, "businessName": "ACME GmbH"},
                      {"id": "22222222-2222-2222-2222-222222222222",
                       "businessName": "ACME GmbH"}]})
    # No address on the ticket either — same-name candidates cannot be
    # disambiguated by the graded address check, so the picker stops the run.
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_address="")
    r = run(monkeypatch, db, stub, f)
    assert r["status"] == "pending_selection"
    assert r["manual"]["decisions"]["template"] == ""
    assert r["manual"]["context"]["fields"]["debtor_name"] == "ACME GmbH"


# --- pure recompose ------------------------------------------------------------


def _manual_from_run(monkeypatch, db):
    fx = company(
        seizures=[OWN, OTHER],
        details={9: {**OWN, "seizedAmount": 138.03},
                 12: {**OTHER, "seizedAmount": 500.0, "amount": 900.0,
                      "creditorName": "AOK PLUS"}})
    r = run(monkeypatch, db, StubBO(fixtures={UUID: fx}), fields())
    return r["manual"]


def test_recompose_same_decisions_reproduces_document(monkeypatch, db, client):
    m = _manual_from_run(monkeypatch, db)
    out = compose_from_decisions(db, m["decisions"], m["context"], m["auto"])
    assert out["declaration"]["template"] == "T2"
    assert out["declaration"]["kind"] == "letter"
    assert out["manual_template"] is False
    assert "Bestehende Pfändungen: Ja" in out["declaration"]["text"]
    assert "\t• " in out["declaration"]["text"]      # reported row bullet


def test_recompose_role_flip_changes_document(monkeypatch, db, client):
    # Operator sets the competing row to 'ignore' and picks T1 -> no bullets,
    # 'Bestehende Pfändungen: Nein', manual_template flag set.
    m = _manual_from_run(monkeypatch, db)
    d = dict(m["decisions"])
    d["seizures"] = [{**row, "role": "ignore" if row["role"] == "report" else row["role"]}
                     for row in d["seizures"]]
    d["template"] = "T1"
    out = compose_from_decisions(db, d, m["context"], m["auto"])
    assert out["manual_template"] is True
    text = out["declaration"]["text"]
    assert "Bestehende Pfändungen: Nein" in text
    assert "\t• " not in text
    assert out["warnings"] == []                     # consistent selection


def test_recompose_email_template_and_subject(monkeypatch, db, client):
    m = _manual_from_run(monkeypatch, db)
    # Auto (T2) composes the LETTER subject; switching to T11 unpinned must
    # re-derive the T11 EMAIL subject, never keep the previous one.
    auto_out = compose_from_decisions(db, m["decisions"], m["context"], m["auto"])
    assert "Drittschuldner" in auto_out["declaration"]["subject"]
    d = {**m["decisions"], "template": "T11", "seizable_eur": "82,41"}
    out = compose_from_decisions(db, d, m["context"], m["auto"])
    assert out["declaration"]["kind"] == "email"
    assert out["declaration"]["subject"].startswith("Konto geschlossen")
    assert "82,41" in out["declaration"]["text"]     # German-comma amount parsed


def test_pinned_subject_survives_template_change(monkeypatch, db, client):
    m = _manual_from_run(monkeypatch, db)
    d = {**m["decisions"], "template": "T11", "subject": "My custom subject",
         "subject_pinned": True}
    out = compose_from_decisions(db, d, m["context"], m["auto"])
    assert out["declaration"]["subject"] == "My custom subject"


def test_recompose_requires_valid_template(db, client):
    with pytest.raises(BigiError):
        compose_from_decisions(db, {"template": ""}, {}, None)
    with pytest.raises(BigiError):
        compose_from_decisions(db, {"template": "T99"}, {}, None)


# --- validation warnings ---------------------------------------------------------


def test_validation_matrix():
    ctx = {"options": {"status_bucket": "OPEN"}}
    rows = [{"id": 1, "role": "report"}, {"id": 2, "role": "own"}]

    w = validate_decisions({"template": "T1", "seizures": rows,
                            "seizable_eur": 1.0}, ctx)
    assert any("T2 fits" in x for x in w)

    w = validate_decisions({"template": "T2", "seizures": [{"id": 2, "role": "own"}],
                            "seizable_eur": 1.0}, ctx)
    assert any("T1 fits" in x for x in w)

    w = validate_decisions({"template": "T2", "seizures": [{"id": 1, "role": "report"}],
                            "seizable_eur": None}, ctx)
    assert any("own case" in x for x in w)
    assert any("seizable amount is empty" in x for x in w)

    w = validate_decisions({"template": "T7", "recipient_email": ""}, ctx)
    assert any("recipient" in x for x in w)

    w = validate_decisions({"template": "T11", "seizable_eur": 5.0,
                            "recipient_email": "a@b.c"}, ctx)
    assert any("OPEN" in x for x in w)

    w = validate_decisions({"template": "T1", "seizures": [],
                            "seizable_eur": 100.0, "available_eur": 50.0},
                           {"options": {"status_bucket": "CLOSED"}})
    assert any("CLOSED" in x for x in w)
    assert any("exceeds the available balance" in x for x in w)


def test_template_catalog_covers_all_templates():
    from app.templates import TEMPLATES

    assert {t["id"] for t in TEMPLATE_CATALOG} == set(TEMPLATES)


# --- parsed-field overrides -------------------------------------------------------


def test_field_overrides_apply_and_rerun_checks(monkeypatch, db, client):
    fx = company(seizures=[OWN], details={9: {**OWN, "seizedAmount": 42.0}})
    stub = StubBO(fixtures={UUID: fx})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(
        db, raw_ticket(fields()),
        field_overrides={"debtor_name": "Edited Name GmbH",
                         "unknown_key": "ignored",
                         "seizure_amount": "3000.00"})   # unchanged -> not listed
    assert r["parsed"]["debtor_name"] == "Edited Name GmbH"
    assert r["parsed"]["edited_fields"] == ["debtor_name"]
    assert any("operator-edited fields: debtor_name" in w
               for w in r["parsed"]["warnings"])


# --- the /compose endpoint ---------------------------------------------------------


def test_compose_endpoint(monkeypatch, db, client):
    m = _manual_from_run(monkeypatch, db)
    body = {"decisions": {**m["decisions"], "template": "T1"},
            "context": {**m["context"], "options": m["options"]},
            "auto": m["auto"]}
    resp = client.post("/api/declaration/compose", json=body)
    assert resp.status_code == 200
    out = resp.json()
    assert out["declaration"]["template"] == "T1"
    assert out["manual_template"] is True
    assert any("T2 fits" in w for w in out["warnings"])  # reported row left

    resp = client.post("/api/declaration/compose", json={"decisions": {}})
    assert resp.status_code == 400


# --- audit fixes: B2 (amount locale), B4 (no [Comment] slot) --------------- #

def test_seizable_us_and_german_amounts_parse_correctly(monkeypatch, db, client):
    # B2: "1,234.56" (US) must NOT become 1.23456 in the letter.
    m = _manual_from_run(monkeypatch, db)
    for raw, want in (("1,234.56", "1.234,56"), ("1.234,56", "1.234,56"),
                      ("82,41", "82,41"), ("1000", "1.000,00")):
        d = {**m["decisions"], "template": "T1", "seizable_eur": raw}
        out = compose_from_decisions(db, d, m["context"], m["auto"])
        assert f"{want} EUR" in out["declaration"]["text"], (raw, want)


def test_non_comment_template_drops_bullets(monkeypatch, db, client):
    # B4: a template without a [Comment] slot must not receive seizure bullets
    # (they would fail the LLM bullet guard and waste a roundtrip).
    m = _manual_from_run(monkeypatch, db)               # has a reported seizure
    d = {**m["decisions"], "template": "T6"}            # T6 has no [Comment]
    out = compose_from_decisions(db, d, m["context"], m["auto"])
    assert "\t• " not in out["declaration"]["text"]
