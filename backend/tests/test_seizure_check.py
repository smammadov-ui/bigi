"""Offline tests for the ongoing-seizure check + alerts (fake BOClient).

Ported from mini's suite; the T1/T2 label moved to the scenario resolver, so
these tests assert on ``processing_count`` (0 -> S1 territory, >=1 -> S2).
"""
from __future__ import annotations

from app.bo_client import BOError
from app.checks import (
    check_alerts,
    check_ongoing_seizures,
    open_alert_rules,
    same_case,
    seizure_description_de,
    strip_jira_links,
)


class FakeClient:
    def __init__(self, seizures=None, details=None, alerts=None,
                 list_exc=None, get_exc=None, alerts_exc=None):
        self._seizures = seizures or []
        self._details = details or {}
        self._alerts = alerts or []
        self.list_exc = list_exc
        self.get_exc = get_exc
        self.alerts_exc = alerts_exc

    def list_seizures(self, company_uuid):
        if self.list_exc is not None:
            raise self.list_exc
        return {"seizures": self._seizures}

    def get_seizure(self, seizure_id):
        if self.get_exc is not None:
            raise self.get_exc
        return self._details.get(seizure_id, {})

    def get_alerts(self, company_uuid):
        if self.alerts_exc is not None:
            raise self.alerts_exc
        return {"items": self._alerts, "totalCount": len(self._alerts)}


# --- strip_jira_links -------------------------------------------------------


def test_strip_smartlink_keeps_text():
    assert strip_jira_links("see [FEI-12|https://jira/x] now") == "see FEI-12 now"


def test_strip_bare_url():
    assert strip_jira_links("ref https://jira.example.com/browse/FEI-9 end") == "ref end"


def test_strip_collapses_double_spaces():
    out = strip_jira_links("a  b   c")
    assert "  " not in out


def test_strip_none():
    assert strip_jira_links(None) == ""


# --- alerts (Step 3) ---------------------------------------------------------


def test_open_alert_rules_only_unresolved():
    items = [
        {"rules": ["MNL21"], "resolvedOn": None},
        {"rules": ["MNL20"], "resolvedOn": "2026-01-01"},  # resolved -> ignored
        {"rules": "MNL22", "resolvedOn": None},            # scalar rule accepted
    ]
    assert open_alert_rules(items) == {"MNL21", "MNL22"}


def test_check_alerts_ok():
    client = FakeClient(alerts=[{"rules": ["MNL21"], "resolvedOn": None}])
    out = check_alerts(client, "u1")
    assert out["open_rules"] == ["MNL21"]
    assert out["assumed"] is False and out["error"] is None


def test_check_alerts_boerror_degrades_assumed():
    client = FakeClient(alerts_exc=BOError("get_alerts", 502, "down"))
    out = check_alerts(client, "u1")
    assert out["open_rules"] == []
    assert out["assumed"] is True
    assert "down" in out["error"]


def test_check_alerts_no_uuid_is_not_assumed():
    out = check_alerts(FakeClient(), "")
    assert out == {"items": [], "open_rules": [], "error": None, "assumed": False}


# --- 0 vs >=1 Processing decisions -------------------------------------------


def test_no_processing_gives_zero_count():
    client = FakeClient(seizures=[{"id": "s1", "status": "Closed"}])
    out = check_ongoing_seizures(client, "u1")
    assert out["processing_count"] == 0
    assert out["seizures"] == []
    assert out["ignored_same_case"] == []
    assert out["error"] is None
    assert out["assumed"] is False


# --- same_case matching -----------------------------------------------------


def test_same_case_exact_and_formatting_insensitive():
    assert same_case("2614/239/24045 - VO 05 - 12619/26 F",
                     "2614/239/24045 - VO 05 - 12619/26 F") is True
    # Different formatting (spaces/dashes/case) but same digits -> match.
    assert same_case("261423924045VO051261926F",
                     "2614/239/24045 - vo 05 - 12619/26 f") is True


def test_same_case_ticket_carries_short_tail():
    # Ticket has only the short reference; BO has the long court-prefixed one.
    assert same_case("2614/239/24045 - VO 05 - 12619/26 F", "12619/26 F") is True


def test_same_case_shared_prefix_does_not_false_match():
    # Two sibling seizures share the court prefix but differ in the tail: the
    # ticket for one must NOT match the other.
    ticket = "2614/239/24045 - VO 05 - 12619/26 F"
    assert same_case("2614/239/24045 - VO 05 - 728/26 F", ticket) is False


def test_same_case_empty_and_short_are_safe():
    assert same_case("2614/239/24045 - VO 05 - 12619/26 F", "") is False
    assert same_case("", "AZ-2026-0001") is False
    assert same_case("26 F", "26 F") is False  # too short (< 6 alnum)


def test_matching_seizure_is_ignored_count_zero():
    seizures = [{"id": "s1", "status": "Processing"}]
    details = {"s1": {"id": "s1", "status": "Processing", "created": "2026-03-01",
                      "caseNumber": "2614/239/24045 - VO 05 - 12619/26 F", "comment": "self"}}
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1", ticket_case_ref="12619/26 F")
    assert out["processing_count"] == 0
    assert out["seizures"] == []
    assert out["own_case_missing"] is False
    assert [i["caseNumber"] for i in out["ignored_same_case"]] == [
        "2614/239/24045 - VO 05 - 12619/26 F"
    ]


def test_only_matching_seizure_ignored_sibling_kept():
    # One seizure matches the ticket (ignored); a sibling with a different tail
    # is a genuine prior seizure and keeps the count at 1.
    seizures = [{"id": "s1", "status": "Processing"}, {"id": "s2", "status": "Processing"}]
    details = {
        "s1": {"id": "s1", "status": "Processing", "created": "2026-02-01",
               "caseNumber": "2614/239/24045 - VO 05 - 12619/26 F"},
        "s2": {"id": "s2", "status": "Processing", "created": "2026-01-01",
               "caseNumber": "2614/239/24045 - VO 05 - 728/26 F"},
    }
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1", ticket_case_ref="12619/26 F")
    assert out["processing_count"] == 1
    assert [s["id"] for s in out["seizures"]] == ["s2"]  # sibling kept
    assert [i["id"] for i in out["ignored_same_case"]] == ["s1"]
    # The sibling (2026-01-01) predates the own case (2026-02-01) -> not "later".
    assert out["ignored_later"] == []


# --- ignore competing seizures created after the own case --------------------


def test_competing_created_after_own_case_is_ignored():
    # Own case created 2026-02-01; a competing seizure created LATER (2026-03-01)
    # is junior to this case -> moved to ignored_later, not counted.
    seizures = [{"id": "own", "status": "Processing"}, {"id": "late", "status": "Processing"}]
    details = {
        "own": {"id": "own", "status": "Processing", "created": "2026-02-01",
                "caseNumber": "2614/239/24045 - VO 05 - 12619/26 F"},
        "late": {"id": "late", "status": "Processing", "created": "2026-03-01",
                 "caseNumber": "2614/239/24045 - VO 05 - 728/26 F"},
    }
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1", ticket_case_ref="12619/26 F")
    assert out["processing_count"] == 0
    assert out["seizures"] == []
    assert [s["id"] for s in out["ignored_later"]] == ["late"]
    assert [i["id"] for i in out["ignored_same_case"]] == ["own"]


def test_only_seizures_after_own_case_are_ignored():
    # A competitor BEFORE the own case is kept; one AFTER it is dropped.
    seizures = [
        {"id": "own", "status": "Processing"},
        {"id": "prior", "status": "Processing"},
        {"id": "late", "status": "Processing"},
    ]
    details = {
        "own": {"id": "own", "status": "Processing", "created": "2026-02-15",
                "caseNumber": "2614/239/24045 - VO 05 - 12619/26 F"},
        "prior": {"id": "prior", "status": "Processing", "created": "2026-01-10",
                  "caseNumber": "OTHER-PRIOR-001"},
        "late": {"id": "late", "status": "Processing", "created": "2026-03-20",
                 "caseNumber": "OTHER-LATE-002"},
    }
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1", ticket_case_ref="12619/26 F")
    assert out["processing_count"] == 1
    assert [s["id"] for s in out["seizures"]] == ["prior"]
    assert [s["id"] for s in out["ignored_later"]] == ["late"]


def test_no_own_case_disables_later_filter():
    # Without the ticket's own seizure there is no cutoff -> every competing
    # Processing seizure is kept and ignored_later stays empty.
    seizures = [{"id": "a", "status": "Processing"}, {"id": "b", "status": "Processing"}]
    details = {
        "a": {"id": "a", "status": "Processing", "created": "2026-01-01", "caseNumber": "CN-A-0001"},
        "b": {"id": "b", "status": "Processing", "created": "2026-09-09", "caseNumber": "CN-B-0002"},
    }
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1", ticket_case_ref="NO-MATCH-9999")
    assert out["processing_count"] == 2
    assert out["ignored_later"] == []
    assert out["own_case_missing"] is True


def test_competing_without_created_is_kept():
    # Own case present; a competing seizure with no `created` cannot be shown to
    # be junior -> kept (safe default), not moved to ignored_later.
    seizures = [{"id": "own", "status": "Processing"}, {"id": "nc", "status": "Processing"}]
    details = {
        "own": {"id": "own", "status": "Processing", "created": "2026-02-01",
                "caseNumber": "2614/239/24045 - VO 05 - 12619/26 F"},
        "nc": {"id": "nc", "status": "Processing", "caseNumber": "OTHER-777-001"},  # no created
    }
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1", ticket_case_ref="12619/26 F")
    assert out["processing_count"] == 1
    assert [s["id"] for s in out["seizures"]] == ["nc"]
    assert out["ignored_later"] == []


def test_two_processing_ordered_and_stripped():
    seizures = [
        {"id": "s1", "status": "Processing"},
        {"id": "s2", "status": {"name": "Processing"}},
        {"id": "s3", "status": "Closed"},  # ignored
    ]
    details = {
        "s1": {"id": "s1", "status": "Processing", "created": "2026-02-10", "comment": "later [X|http://j/x]"},
        "s2": {"id": "s2", "status": "Processing", "created": "2026-01-05", "comment": "earlier https://j/y note"},
    }
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1")
    assert out["processing_count"] == 2
    # ordered ascending by created -> s2 (Jan) then s1 (Feb)
    assert [s["id"] for s in out["seizures"]] == ["s2", "s1"]
    assert out["seizures"][0]["comment"] == "earlier note"
    assert out["seizures"][1]["comment"] == "later X"
    assert out["assumed"] is False


def test_boerror_on_list_gives_assumed_zero():
    client = FakeClient(list_exc=BOError("list_seizures", 502, "down"))
    out = check_ongoing_seizures(client, "u1")
    assert out["processing_count"] == 0
    assert out["seizures"] == []
    assert out["assumed"] is True
    assert "down" in out["error"]


def test_boerror_on_get_detail_keeps_listing_row():
    # A failed detail read must not lose the seizure: the listing row alone
    # still carries id/case/created (engine behavior — more resilient than
    # mini's assume-T1).
    seizures = [{"id": "s1", "status": "Processing", "created": "2026-01-01",
                 "caseNumber": "CN-1", "comment": "from listing"}]
    client = FakeClient(seizures=seizures, get_exc=BOError("get_seizure", 500, "boom"))
    out = check_ongoing_seizures(client, "u1")
    assert out["assumed"] is False
    assert out["processing_count"] == 1
    assert out["seizures"][0]["caseNumber"] == "CN-1"
    assert out["seizures"][0]["comment"] == "from listing"


def test_no_company_uuid_skips_assumed():
    client = FakeClient()
    out = check_ongoing_seizures(client, "")
    assert out["processing_count"] == 0
    assert out["assumed"] is True
    assert "account not resolved" in out["error"]


def test_seizure_row_shape():
    seizures = [{"id": "s1", "status": "Processing"}]
    details = {"s1": {"id": "s1", "status": "Processing", "created": "2026-03-01",
                      "caseNumber": "CN-1", "comment": "plain"}}
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1")
    row = out["seizures"][0]
    assert set(row.keys()) == {
        "id", "caseNumber", "status", "created", "comment", "description_de",
        "seized_amount", "claim_amount", "client_total",
    }
    assert row["caseNumber"] == "CN-1"
    assert row["created"] == "2026-03-01"
    # Detail had no amounts -> these stay None (don't break the row).
    assert row["seized_amount"] is None
    assert row["client_total"] is None
    # No structured creditor/amount either -> the raw comment is the bullet.
    assert row["description_de"] == "plain"


# --- seizure_description_de ---------------------------------------------------


def test_description_de_from_structured_fields():
    # A Porters-created seizure: generic comment, real facts in structured fields.
    detail = {
        "comment": "The seizure was created by the Porters",
        "issuedBy": "BKK Linde",
        "creditorName": "BKK Linde",
        "issuedOn": "2026-06-08T00:00:00Z",
        "businessName": "Luca Durante",
        "amount": 1513.23,
        "seizureAmount": 1513.23,
    }
    assert seizure_description_de(detail) == (
        "Wir haben eine Pfändung von BKK Linde, ausgestellt am 08.06.2026, "
        "für Luca Durante erhalten. Der Pfändungsbetrag beträgt 1.513,23 EUR."
    )


def test_description_de_missing_optional_parts():
    assert seizure_description_de({"issuedBy": "Hauptzollamt Kiel"}) == (
        "Wir haben eine Pfändung von Hauptzollamt Kiel erhalten."
    )
    assert seizure_description_de({"amount": 100}) == (
        "Wir haben eine Pfändung erhalten. Der Pfändungsbetrag beträgt 100,00 EUR."
    )


def test_description_de_empty_without_creditor_or_amount():
    assert seizure_description_de({}) == ""
    assert seizure_description_de({"comment": "The seizure was created by the Porters"}) == ""


def test_description_de_used_for_row_when_structured_data_present():
    seizures = [{"id": "s1", "status": "Processing"}]
    details = {"s1": {"id": "s1", "status": "Processing", "created": "2026-03-01",
                      "caseNumber": "CN-1",
                      "comment": "The seizure was created by the Porters",
                      "issuedBy": "BKK Linde", "issuedOn": "2026-06-08T00:00:00Z",
                      "businessName": "Luca Durante", "amount": 1513.23}}
    client = FakeClient(seizures=seizures, details=details)
    out = check_ongoing_seizures(client, "u1")
    row = out["seizures"][0]
    # Raw comment kept for the UI; the letter bullet uses the structured facts.
    assert row["comment"] == "The seizure was created by the Porters"
    assert row["description_de"].startswith("Wir haben eine Pfändung von BKK Linde")
    assert "1.513,23 EUR" in row["description_de"]


def test_seizure_row_carries_amounts():
    seizures = [{"id": "s1", "status": "Processing"}]
    details = {"s1": {"id": "s1", "status": "Processing", "created": "2026-03-01",
                      "seizedAmount": 2204.08, "amount": 4471.40,
                      "balance": {"clientTotal": 0.0,
                                  "wallets": [{"type": "seizure", "balance": 2204.08}]}}}
    client = FakeClient(seizures=seizures, details=details)
    row = check_ongoing_seizures(client, "u1")["seizures"][0]
    assert row["seized_amount"] == 2204.08
    assert row["claim_amount"] == 4471.40
    assert row["client_total"] == 0.0


# --- real-BO rule-code variants ---------------------------------------------------


def test_canonical_rule_variants():
    from app.checks import canonical_rule

    assert canonical_rule("MNL21") == "MNL21"
    assert canonical_rule("MNL-21-FP") == "MNL21"
    assert canonical_rule("mnl 22") == "MNL22"
    assert canonical_rule("MNL_20") == "MNL20"
    assert canonical_rule("TM-OTHER-9") == "TM-OTHER-9"   # non-MNL passes through
    assert canonical_rule(None) == ""


def test_open_alert_rules_normalizes_variants():
    items = [
        {"rules": ["MNL-21-FP"], "resolvedOn": None},
        {"rules": ["mnl 22"], "resolvedOn": None},
        {"rules": ["MNL-20-XX"], "resolvedOn": "2026-01-01"},  # resolved -> ignored
    ]
    assert open_alert_rules(items) == {"MNL21", "MNL22"}
