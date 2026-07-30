"""All 11 scenarios end-to-end through ``run_pipeline`` (StubBO, offline).

Each case builds a real raw ticket (tests.fixtures.raw_ticket), stubs BO with
per-company fixtures, and asserts scenario, template, document kind, and the
key figures/flags in the composed German text. No LLM key -> deterministic.
"""
from __future__ import annotations

import pytest

from app import pipeline
from tests.fixtures import CASE_REF, IBAN, UUID, company, fields, raw_ticket
from tests.stub_bo import StubBO


def run(monkeypatch, db, fx_or_stub, f=None):
    stub = fx_or_stub if isinstance(fx_or_stub, StubBO) else StubBO(fixtures={UUID: fx_or_stub})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    return pipeline.run_pipeline(db, raw_ticket(f))


OWN = {"id": 9, "caseNumber": "261423924045VO05", "status": "Processing",
       "created": "2026-02-02T10:00:00Z"}
PRIOR = {"id": 1, "caseNumber": "999/888/77", "status": "Processing",
         "created": "2026-01-15T10:00:00Z"}
JUNIOR = {"id": 2, "caseNumber": "555/444/33", "status": "Processing",
          "created": "2026-03-01T10:00:00Z"}


# --- S1 -----------------------------------------------------------------------


def test_s1_normal_tpd(monkeypatch, db, client):
    r = run(monkeypatch, db, company())
    assert r["status"] == "ok"
    assert r["scenario"] == "S1"
    assert r["plan"]["template"] == "T1" and r["plan"]["action"] == "letter"
    d = r["declaration"]
    assert d["kind"] == "letter"
    assert "Aktenzeichen" in d["subject"] and CASE_REF in d["subject"]
    assert "Kundenbeziehung besteht: Ja" in d["text"]
    assert "Bestehende Pfändungen: Nein" in d["text"]
    assert "[" not in d["text"]
    # min(claim 3000, available 5000) -> 3.000,00 (own case missing -> warning)
    assert "3.000,00 EUR" in d["text"]
    assert any("own-case seizure not found" in w for w in r["warnings"])


def test_s1_own_case_does_not_flip_to_s2(monkeypatch, db, client):
    fx = company(seizures=[OWN],
                 details={9: {**OWN, "seizedAmount": 1200.0, "comment": "own case"}})
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S1"
    assert len(r["seizure_check"]["ignored_same_case"]) == 1
    assert r["amount"]["seized_eur"] == 1200.0
    assert r["amount"]["source"] == "bo_own_case_seized_amount"
    assert "1.200,00 EUR" in r["declaration"]["text"]


# --- S2 -----------------------------------------------------------------------


def test_s2_competing_prior_with_structured_bullet_and_junior_filter(monkeypatch, db, client):
    det = {
        1: {**PRIOR, "issuedBy": "Finanzamt Leipzig II", "amount": 900.5,
            "issuedOn": "2026-01-10", "comment": "See [t|https://jira/PF-1]",
            "seizedAmount": 900.5, "balance": {"clientTotal": 4100.0}},
        2: {**JUNIOR, "issuedBy": "Finanzamt Bremen", "amount": 50.0},
        9: {**OWN, "seizedAmount": 700.0},
    }
    fx = company(seizures=[PRIOR, JUNIOR, OWN], details=det)
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S2"
    assert r["plan"]["template"] == "T2"
    sc = r["seizure_check"]
    assert sc["processing_count"] == 1                 # junior + own filtered
    assert [s["id"] for s in sc["ignored_later"]] == [2]
    text = r["declaration"]["text"]
    assert "Bestehende Pfändungen: Ja" in text
    assert "\t• Wir haben eine Pfändung von Finanzamt Leipzig II" in text
    assert "900,50 EUR" in text
    assert text.count("\t•") == 1                      # one bullet per competitor
    # own-case seizedAmount is the declared figure
    assert r["amount"]["seized_eur"] == 700.0
    assert "700,00 EUR" in text
    # held funds mirror the seizure records (prior 900.50 + own 700)
    assert r["balance"]["held_eur"] == 1600.5
    assert r["balance"]["client_total_eur"] == 4100.0


# --- S3 -----------------------------------------------------------------------


def test_s3_closed_before_ticket(monkeypatch, db, client):
    fx = company(status="AccountClosed", updated="2026-01-05T00:00:00Z")
    r = run(monkeypatch, db, fx)   # received 2026-02-01
    assert r["scenario"] == "S3"
    d = r["declaration"]
    assert d["template"] == "T6" and d["kind"] == "letter"
    assert "Kundenbeziehung besteht: Nein" in d["text"]
    assert "Bestehende Pfändungen: N/A" in d["text"]
    assert "in Höhe von 0,00 EUR" in d["text"]


def test_s3_onboarding(monkeypatch, db, client):
    r = run(monkeypatch, db, company(status="ApplicationInProgress"))
    assert r["scenario"] == "S3"
    assert r["declaration"]["template"] == "T6"


def test_closed_after_ticket_routes_out(monkeypatch, db, client):
    fx = company(status="AccountClosed", updated="2026-03-01T00:00:00Z")
    r = run(monkeypatch, db, fx)   # received 2026-02-01 -> closed AFTER
    assert r["scenario"] == "ROUTED_OUT"
    assert r["declaration"] is None
    assert any("closed on/after" in n for n in r["plan"]["notes"])


# --- S4 -----------------------------------------------------------------------


def test_s4_no_iban(monkeypatch, db, client):
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_name="Unknown Ltd")
    r = run(monkeypatch, db, StubBO(), f)   # empty BO -> nothing found
    assert r["scenario"] == "S4_NO_IBAN"
    d = r["declaration"]
    assert d["template"] == "T7" and d["kind"] == "email"
    assert "IBAN mitzuteilen" in d["text"]
    assert d["subject"].startswith("Rückfrage IBAN")


def test_s4_iban_provided_but_unknown(monkeypatch, db, client):
    f = fields(company_uuid="", debtor_register_number="", debtor_name="Unknown Ltd")
    r = run(monkeypatch, db, StubBO(), f)   # IBAN on ticket, no BO hit
    assert r["scenario"] == "S4_IBAN"
    assert r["declaration"]["template"] == "T8"
    assert "nicht erfasst" in r["declaration"]["text"]


def test_confirmation_mismatch_is_s4(monkeypatch, db, client):
    # Resolved by ticket UUID but neither IBAN nor address confirms -> S4.
    fx = company(wallets=[{"id": "w1", "iban": "DE02120300000000202051",
                           "name": "Main", "balance": 10.0, "currency": "EUR"}],
                 address={"street": "Elsewhere 9", "zip": "99999", "city": "X"})
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S4_IBAN"
    assert r["account"]["outcome"] == "NO_MATCH"


# --- S5 -----------------------------------------------------------------------


def test_s5_person_vs_company(monkeypatch, db, client):
    f = fields(debtor_dob="1980-05-05", debtor_register_number="")
    r = run(monkeypatch, db, StubBO(fixtures={UUID: company()}), f)
    assert r["scenario"] == "S5"
    d = r["declaration"]
    assert d["template"] == "T9" and d["kind"] == "email"
    assert "Privatperson" in d["text"]


# --- S6 -----------------------------------------------------------------------


def test_s6a_closing_covered_by_processing_seizure(monkeypatch, db, client):
    det = {1: {**PRIOR, "issuedBy": "Finanzamt Köln-Süd", "amount": 500.0}}
    fx = company(status="ClosureScheduled", seizures=[PRIOR], details=det)
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S6A"
    d = r["declaration"]
    assert d["template"] == "T10" and d["kind"] == "email"
    assert CASE_REF in d["text"]              # [case references]


def test_s6a_closing_zero_balance(monkeypatch, db, client):
    fx = company(status="ClosureScheduled",
                 wallets=[{"id": "w1", "iban": IBAN, "name": "Main",
                           "balance": 0.0, "currency": "EUR"}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S6A"


def test_s6b_closing_with_balance(monkeypatch, db, client):
    fx = company(status="ClosureScheduled")   # balance 5000, claim 3000
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S6B"
    d = r["declaration"]
    assert d["template"] == "T11" and d["kind"] == "email"
    assert "Restbetrag in Höhe von 3.000,00 EUR" in d["text"]
    assert r["amount"]["source"] == "min_claim_available"


def test_s6_closing_with_unknown_balance_routes_out(monkeypatch, db, client):
    stub = StubBO(fixtures={UUID: company(status="ClosureScheduled")}, fail={"wallets"})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw_ticket(fields(seized_iban="")))
    assert r["scenario"] == "ROUTED_OUT"
    assert any("operator review" in n for n in r["plan"]["notes"])
    assert r["declaration"] is None


# --- alert-driven scenarios -----------------------------------------------------


def test_insolvency_mnl21(monkeypatch, db, client):
    fx = company(alerts=[{"rules": ["MNL21"], "resolvedOn": None}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "INSOLVENCY"
    d = r["declaration"]
    assert d["template"] == "T4" and d["kind"] == "email"
    assert "Insolvenzverfahrens" in d["text"]
    assert r["alerts"]["open_rules"] == ["MNL21"]


def test_rfi_mnl22(monkeypatch, db, client):
    fx = company(alerts=[{"rules": ["MNL22"], "resolvedOn": None}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "RFI"
    d = r["declaration"]
    assert d["template"] == "T5" and d["kind"] == "guidance"
    assert "Auskunftsersuchen" in d["text"]


def test_resolved_alert_does_not_branch(monkeypatch, db, client):
    fx = company(alerts=[{"rules": ["MNL21"], "resolvedOn": "2026-01-01"}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S1"


def test_other_open_alert_routes_out(monkeypatch, db, client):
    fx = company(alerts=[{"rules": ["MNL9"], "resolvedOn": None}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "ROUTED_OUT"
    assert r["declaration"] is None


def test_restricted_account_routes_out(monkeypatch, db, client):
    r = run(monkeypatch, db, company(status="AccountBlocked"))
    assert r["scenario"] == "ROUTED_OUT"
    assert any("restricted" in n.lower() for n in r["plan"]["notes"])


# --- every scenario is reachable (closed set) -----------------------------------


def test_scenario_template_map_is_total():
    from app.schemas import SCENARIOS
    from app.templates import SCENARIO_TEMPLATE
    assert set(SCENARIO_TEMPLATE) == set(SCENARIOS)


def test_closed_account_with_epoch_status_date(monkeypatch, db, client):
    # Real BO regression (FPOPCL ticket): epoch-ms accountStatusUpdated on a
    # CLOSED account crashed the resolver. Closed 2026-01-05 < received
    # 2026-02-01 -> S3.
    fx = company(status="AccountClosed", updated=1767571200000)  # 2026-01-05
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S3"
    assert r["declaration"]["template"] == "T6"


def test_closed_after_ticket_with_epoch_date_routes_out(monkeypatch, db, client):
    fx = company(status="AccountClosed", updated=1772323200000)  # 2026-03-01
    r = run(monkeypatch, db, fx)   # received 2026-02-01
    assert r["scenario"] == "ROUTED_OUT"
