"""All 11 scenarios end-to-end through ``run_pipeline`` (StubBO, offline).

Each case builds a real raw ticket (tests.fixtures.raw_ticket), stubs BO with
per-company fixtures, and asserts scenario, template, document kind, and the
key figures/flags in the composed German text. No LLM key -> deterministic.
"""
from __future__ import annotations

import pytest

from app import pipeline
from tests.fixtures import CASE_REF, IBAN, UUID, UUID2, company, fields, raw_ticket
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
    f = fields(debtor_dob="1980-05-05", debtor_register_number="",
               debtor_name="Hamza Bosnjak")
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


def test_other_open_alert_is_note_not_blocker(monkeypatch, db, client):
    # Ops policy: only MNL-20/21/22 drive decisions; FCRM/TM/SNC alerts are
    # unrelated monitoring work (live anchor: FPOPCL-31056, 'FCRM FP-2 PROD').
    fx = company(alerts=[{"rules": ["FCRM FP-2 PROD"], "resolvedOn": None}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S1"
    assert any("ignored per ops policy" in n for n in r["plan"]["notes"])
    assert r["declaration"]["template"] == "T1"


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


def test_closed_bare_account_resolves_s3_not_s4(monkeypatch, db, client):
    # FPOPCL-24636 class: ticket received AFTER closure; BO has no wallets/
    # address/type left for the account. Must be S3 (T6), not S4 (T7).
    fx = company(status="AccountClosed", updated="2026-01-05T00:00:00Z", wallets=[])
    fx["overview"] = {}
    fx["cdd"] = {}
    fx["short_info"] = {"id": UUID, "businessName": "ACME GmbH",
                        "status": {"accountStatus": "AccountClosed"}}
    stub = StubBO(fixtures={UUID: fx}, search_map={IBAN: UUID})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw_ticket(fields(seized_iban="")))
    assert r["scenario"] == "S3"
    d = r["declaration"]
    assert d["template"] == "T6" and d["kind"] == "letter"
    assert "Kundenbeziehung besteht: Nein" in d["text"]


def test_matched_but_unreadable_status_routes_out(monkeypatch, db, client):
    # Strong identity accepted with NO readable account status -> never S1.
    fx = company(status="", wallets=[])
    fx["overview"] = {}
    fx["cdd"] = {}
    fx["short_info"] = {"id": UUID, "businessName": "ACME GmbH", "status": {}}
    fx["search_items"] = []
    stub = StubBO(fixtures={UUID: fx})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw_ticket(fields(seized_iban="")))
    assert r["scenario"] == "ROUTED_OUT"
    assert any("not readable" in n for n in r["plan"]["notes"])


# --- Step-0 classification (live cases FPOPCL-24619 / FPOPCL-24605) ----------------


CRIMINAL_TICKET = """We received a seizure_warrant request from Staatsanwaltschaft Hannover issued on 2026-05-28 for Alexandru Stog. The amount of the seizure is 43000.00.
Additional information
document type: seizure
seizure type: seizure_warrant
seizure amount: 43000.00
date received: 2026-06-03
debtor name: Alexandru Stog
case references: NZS 4111 Js 138236/25 VRs
seized IBANs: DE89370400440532013000
"""

RFI_TICKET = """We received an RFI of type public_prosecutor_investigation from Staatsanwaltschaft Düsseldorf issued on 2026-05-15 regarding Hasan Kaplan.
Additional information
definitive match:

potential match: 11111111-1111-1111-1111-111111111111

rfi type: public_prosecutor_investigation

subject name: Hasan Kaplan

subject IBANs: DE89370400440532013000

requester name: Staatsanwaltschaft Düsseldorf

case references: 123 Js 1092/26

subject date of birth: 1977-01-15

subject Address: Hauptstr. 1, , 60311, Frankfurt
"""


def test_criminal_warrant_routes_out_without_bo_calls(monkeypatch, db, client):
    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, CRIMINAL_TICKET)
    assert r["scenario"] == "ROUTED_OUT"
    assert r["declaration"] is None
    assert r["account"] is None
    assert stub.calls == []                       # confidential: BO untouched
    assert any("criminal" in n.lower() for n in r["plan"]["notes"])
    assert any("tip" in n.lower() for n in r["plan"]["notes"])


def test_rfi_ticket_forced_to_t5(monkeypatch, db, client):
    fx = company(type_="Freelancer")
    stub = StubBO(fixtures={UUID: fx})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, RFI_TICKET)
    assert r["scenario"] == "RFI"
    d = r["declaration"]
    assert d["template"] == "T5" and d["kind"] == "guidance"
    # Subject/requester aliases parsed -> account resolved for data gathering.
    assert r["parsed"]["debtor_name"] == "Hasan Kaplan"
    assert r["parsed"]["creditor_name"] == "Staatsanwaltschaft Düsseldorf"
    assert r["account"]["company_uuid"] == UUID   # resolved via potential-match UUID


def test_prosecutor_creditor_without_warrant_type_routes_out(monkeypatch, db, client):
    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    raw = raw_ticket(fields(creditor_name="Staatsanwaltschaft Hannover"))
    r = pipeline.run_pipeline(db, raw)
    assert r["scenario"] == "ROUTED_OUT"
    assert stub.calls == []


def test_civil_public_creditor_still_flows(monkeypatch, db, client):
    r = run(monkeypatch, db, company())          # Finanzamt Bremen fixture
    assert r["scenario"] == "S1"


# --- S5 with unconfirmed-but-strong identity (FPOPCL-23266) ------------------------


def test_s5_fires_on_strong_identity_despite_address_mismatch(monkeypatch, db, client):
    # Person's private address != company's business address; identity via
    # definitive ticket UUID -> S5, not S4.
    fx = company(address={"street": "Bürostr. 2", "zip": "99999", "city": "B"})
    f = fields(debtor_dob="1980-05-05", debtor_register_number="", seized_iban="",
               debtor_name="Hamza Bosnjak", debtor_address="Privatweg 9, 11111 A")
    stub = StubBO(fixtures={UUID: fx})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["scenario"] == "S5"
    assert r["declaration"]["template"] == "T9"


def test_s5_freelancer_lookup_offers_candidates(monkeypatch, db, client):
    fx = company()                                 # the Company account
    freelancer_item = {"id": UUID2, "businessName": "ACME GmbH", "regNumber": "",
                       "type": "Freelancer", "accountStatus": "AccountOpened"}
    stub = StubBO(fixtures={UUID: fx},
                  search_items_map={"Hamza Bosnjak": [freelancer_item]})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    f = fields(debtor_dob="1980-05-05", debtor_register_number="",
               debtor_name="Hamza Bosnjak")
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["status"] == "pending_selection"
    ids = [c["id"] for c in r["account"]["candidates"]]
    assert UUID2 in ids and UUID in ids            # freelancer + the company


# --- seizure-aware AccountBlocked handling (ops SOP: blocked until resolved) -------


def test_blocked_with_own_case_proceeds_s1(monkeypatch, db, client):
    # Guide case 1 drift (FPOPCL-24373): blocked BECAUSE the seizure executed.
    fx = company(status="AccountBlocked", seizures=[OWN],
                 details={9: {**OWN, "seizedAmount": 360.16}})
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S1"
    assert "360,16 EUR" in r["declaration"]["text"]
    assert any("stays restricted until the seizure resolves" in n for n in r["plan"]["notes"])


def test_blocked_with_competing_processing_is_s2(monkeypatch, db, client):
    det = {1: {**PRIOR, "issuedBy": "Finanzamt Leipzig II", "amount": 900.5}}
    fx = company(status="AccountBlocked", seizures=[PRIOR], details=det)
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S2"
    assert r["declaration"]["template"] == "T2"


def test_blocked_without_seizures_still_routes_out(monkeypatch, db, client):
    # Compliance-blocked (FPOPCL-14753's sibling): no seizure activity -> operator.
    r = run(monkeypatch, db, company(status="AccountBlocked"))
    assert r["scenario"] == "ROUTED_OUT"
    assert any("restricted" in n.lower() for n in r["plan"]["notes"])


def test_blocked_with_assumed_seizures_routes_out(monkeypatch, db, client):
    stub = StubBO(fixtures={UUID: company(status="AccountBlocked")},
                  fail={"list_seizures"})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw_ticket())
    assert r["scenario"] == "ROUTED_OUT"


def test_limited_account_with_seizures_proceeds(monkeypatch, db, client):
    fx = company(status="LimitedAccount", seizures=[OWN],
                 details={9: {**OWN, "seizedAmount": 1.0}})
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S1"
    assert any("LimitedAccount with active seizure" in n for n in r["plan"]["notes"])


def test_limited_account_without_seizures_routes_out(monkeypatch, db, client):
    r = run(monkeypatch, db, company(status="LimitedAccount"))
    assert r["scenario"] == "ROUTED_OUT"


def test_fpopcl31056_shape_limited_plus_fcrm_alert_is_s2(monkeypatch, db, client):
    det = {1: {**PRIOR, "issuedBy": "Finanzamt Leipzig II", "amount": 900.5,
               "seizedAmount": 600.0},
           9: {**OWN, "seizedAmount": 65.88}}
    fx = company(status="LimitedAccount", seizures=[PRIOR, OWN], details=det,
                 alerts=[{"rules": ["FCRM FP-2 PROD"], "resolvedOn": None}])
    r = run(monkeypatch, db, fx)
    assert r["scenario"] == "S2"
    assert r["declaration"]["template"] == "T2"
    notes = " | ".join(r["plan"]["notes"])
    assert "ignored per ops policy" in notes and "LimitedAccount with active seizure" in notes


# --- Repeal / Restriction documents (ops SOP Step 5) -------------------------------


REPEAL_TICKET = """We received a seizure_repeal request from Finanzamt Bremen issued on 2026-06-20 for ACME GmbH.
Additional information
seizure type: seizure_repeal
date received: 2026-06-22
debtor name: ACME GmbH
case references: 2614/239/24045 - VO 05
definitive match: 11111111-1111-1111-1111-111111111111
seized IBANs: DE89370400440532013000
"""


def test_repeal_ticket_routes_out_with_guidance(monkeypatch, db, client):
    fx = company(seizures=[OWN], details={9: {**OWN, "seizedAmount": 500.0}})
    stub = StubBO(fixtures={UUID: fx})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, REPEAL_TICKET)
    assert r["scenario"] == "ROUTED_OUT"
    assert r["declaration"] is None
    assert any("Refund" in n or "refund" in n for n in r["plan"]["notes"])
    # Account + seizures still resolved so the operator sees WHICH seizure.
    assert r["account"]["company_uuid"] == UUID
    assert len(r["seizure_check"]["ignored_same_case"]) == 1


def test_restriction_ticket_routes_out_with_guidance(monkeypatch, db, client):
    raw = REPEAL_TICKET.replace("seizure_repeal", "seizure_restriction")
    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw)
    assert r["scenario"] == "ROUTED_OUT"
    assert any("update the existing seizure's amount" in n for n in r["plan"]["notes"])
    assert r["declaration"] is None


def test_german_aufhebung_detected_as_repeal(monkeypatch, db, client):
    raw = raw_ticket().replace(
        "We received a seizure request from Finanzamt Bremen issued on 2026-01-20.",
        "Aufhebungsbeschluss: die Pfändung wird aufgehoben (Aufhebung der Vollstreckung).")
    stub = StubBO(fixtures={UUID: company()})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    r = pipeline.run_pipeline(db, raw)
    assert r["scenario"] == "ROUTED_OUT"
    assert any("Repeal" in n for n in r["plan"]["notes"])


def test_herabsetzung_detected_as_restriction():
    from app.classify import RESTRICTION, classify_ticket
    kind, _ = classify_ticket("Herabsetzung des Pfändungsbetrages auf 1.000,00 EUR", {})
    assert kind == RESTRICTION


def test_repeal_beats_criminal_markers():
    # An Aufhebungsbeschluss may quote the prosecutor + Js docket of the
    # original case — it is still a repeal, not a new criminal seizure.
    from app.classify import REPEAL, classify_ticket
    kind, _ = classify_ticket(
        "We received a seizure_repeal request from Staatsanwaltschaft Hannover",
        {"seizure_type": "seizure_repeal", "case_references": "4111 Js 138236/25"})
    assert kind == REPEAL


def test_plain_seizure_not_misclassified():
    from app.classify import CIVIL, classify_ticket
    kind, _ = classify_ticket(
        "We received a public_creditor_seizure request from Finanzamt Bremen issued on 2026-01-20",
        {"seizure_type": "public_creditor_seizure", "creditor_name": "Finanzamt Bremen"})
    assert kind == CIVIL


def test_company_debtor_with_dob_is_not_s5(monkeypatch, db, client):
    # FPOPCL-31103: Porters filled the LR's DOB on a company-debtor ticket.
    fx = company(seizures=[OWN], details={9: {**OWN, "seizedAmount": 72.62}})
    f = fields(debtor_dob="1985-03-03", debtor_register_number="")   # name: ACME GmbH
    r = run(monkeypatch, db, StubBO(fixtures={UUID: fx}), f)
    assert r["scenario"] == "S1"
    assert r["declaration"]["template"] == "T1"
    assert "72,62 EUR" in r["declaration"]["text"]
