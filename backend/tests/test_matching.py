"""Confirmation + status-bucket tests (Step 1.4 + Step 2) for app.matching."""
from __future__ import annotations

from app.matching import is_physical_person, match_account, status_bucket
from tests.fixtures import IBAN, UUID, company, fields
from tests.stub_bo import StubBO


# --- status buckets -----------------------------------------------------------


def test_status_buckets():
    assert status_bucket("AccountOpened") == "OPEN"
    assert status_bucket("AccountClosed") == "CLOSED"
    assert status_bucket("ClosureScheduled") == "CLOSING"
    assert status_bucket("WithdrawalOfFunds") == "CLOSING"
    assert status_bucket("AccountBlocked") == "RESTRICTED"
    assert status_bucket("LimitedAccount") == "RESTRICTED"
    assert status_bucket("ApplicationInProgress") == "ONBOARDING"
    assert status_bucket("KycInfoRequest") == "ONBOARDING"
    assert status_bucket("SomethingNew") == "UNKNOWN"
    assert status_bucket("") == "UNKNOWN"


# --- confirmation -------------------------------------------------------------


def test_iban_match_overrides_address_mismatch():
    fx = company(address={"street": "Elsewhere 9", "zip": "99999", "city": "Nowhere"})
    out = match_account(StubBO(fixtures={UUID: fx}), fields())
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "iban"


def test_company_requires_address_match():
    # No IBAN on the ticket -> Company falls back to the address (postcode) rule.
    f = fields(seized_iban="")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "address"


def test_company_address_mismatch_is_no_match():
    f = fields(seized_iban="", debtor_address="Andere Str. 5, 99999 Anderswo")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["outcome"] == "NO_MATCH"


def test_freelancer_dob_match_when_address_differs():
    fx = company(type_="Freelancer",
                 address={"street": "Elsewhere 9", "zip": "99999", "city": "X"})
    f = fields(seized_iban="", debtor_dob="1980-05-05", debtor_register_number="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "dob"


def test_freelancer_both_mismatch_is_no_match():
    fx = company(type_="Freelancer",
                 address={"street": "Elsewhere 9", "zip": "99999", "city": "X"})
    f = fields(seized_iban="", debtor_dob="1999-01-01", debtor_register_number="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "NO_MATCH"


def test_person_vs_company_override():
    # Physical person (DOB present, no register number) matched to a Company.
    f = fields(debtor_dob="1980-05-05", debtor_register_number="")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["outcome"] == "PERSON_VS_COMPANY"


def test_physical_person_heuristic():
    assert is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": ""})
    assert not is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": "HRB 1"})
    assert not is_physical_person({"debtor_dob": "", "debtor_register_number": ""})


def test_seized_iban_derived_from_main_wallet():
    f = fields(seized_iban="")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["seized_iban"] == IBAN
    assert out["seized_iban_source"] == "main_wallet"
    assert out["main_wallet"]["iban"] == IBAN


def test_short_info_fallback_to_search(monkeypatch):
    # short-info gated (406) -> account item comes from cstools_search (by the
    # seized IBAN, mirroring the real endpoint's lookup behavior).
    stub = StubBO(fixtures={UUID: company()}, fail={"cstools_short_info"},
                  search_map={IBAN: UUID})
    out = match_account(stub, fields())
    assert out["account_status"] == "AccountOpened"
    assert out["outcome"] == "MATCH"


def test_wallets_failure_reports_error_not_zero():
    stub = StubBO(fixtures={UUID: company()}, fail={"wallets"})
    out = match_account(stub, fields())
    assert out["wallets_items"] == []
    assert out["wallets_error"] is not None
    # No wallet IBANs to confirm against -> Company falls back to address.
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "address"


# --- real-BO response quirks ------------------------------------------------------


def test_cdd_dob_values_on_child_items():
    # Real cdd-profile: the parameter node holds its values on CHILD items.
    cdd = {
        "sections": [{
            "subSections": [{
                "parameters": [{
                    "parameter": "PersonBirthdate",
                    "items": [{"values": ["1980-05-05"], "properties": {}}],
                }],
            }],
        }],
    }
    from app.matching import _cdd_dob
    assert _cdd_dob(cdd) == "1980-05-05"


def test_cdd_dob_value_scalar_on_child():
    cdd = {"sections": [{"parameters": [{"parameter": "PersonBirthdate",
                                         "items": [{"value": "05.05.1980"}]}]}]}
    from app.matching import _cdd_dob
    assert _cdd_dob(cdd) == "05.05.1980"


def test_cdd_dob_absent_returns_empty():
    from app.matching import _cdd_dob
    assert _cdd_dob({"sections": [{"parameters": [{"parameter": "CompanyLegalForm",
                                                   "items": [{"values": ["GmbH"]}]}]}]}) == ""


def test_closed_account_status_updated_enriched_via_search():
    # Real short-info carries NO accountStatusUpdated; for a CLOSED account the
    # date decides S3 vs routed-out, so it is enriched from cstools_search.
    fx = company(status="AccountClosed", updated="2026-03-01T00:00:00Z")
    fx["short_info"] = {"id": UUID, "businessName": "ACME GmbH",
                        "status": {"accountStatus": "AccountClosed"}}  # no date
    stub = StubBO(fixtures={UUID: fx}, search_map={IBAN: UUID})
    out = match_account(stub, fields())
    assert out["status_bucket"] == "CLOSED"
    assert out["account_status_updated"] == "2026-03-01"   # normalized to ISO date
    assert any("enriched via cstools_search" in r for r in out["reasons"])


def test_closed_account_enrichment_failure_degrades_gracefully():
    fx = company(status="AccountClosed")
    fx["short_info"] = {"id": UUID, "status": {"accountStatus": "AccountClosed"}}

    class SearchFailsAfterIdentify(StubBO):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.search_calls = 0

        def cstools_search(self, text):
            self.search_calls += 1
            from app.bo_client import BOError
            raise BOError("cstools_search", 502, "down")

    out = match_account(SearchFailsAfterIdentify(fixtures={UUID: fx}), fields())
    assert out["account_status_updated"] == ""
    assert any("closed-before-ticket" in r for r in out["reasons"])


def test_epoch_account_status_updated_normalized():
    # Real BO: accountStatusUpdated is an epoch-ms INTEGER. It must normalize
    # to an ISO date and never crash the closed-before/after slice.
    fx = company(status="AccountClosed", updated=1769904000000)  # 2026-02-01
    out = match_account(StubBO(fixtures={UUID: fx}, search_map={IBAN: UUID}), fields())
    assert out["account_status_updated"] == "2026-02-01"
    assert out["status_bucket"] == "CLOSED"


# --- closed-account confirmation (FPOPCL-24636 class of tickets) -----------------


def _closed_bare_fixture(updated="2026-01-05T00:00:00Z"):
    """A CLOSED account as real BO serves it: no wallets, no address, no type."""
    fx = company(status="AccountClosed", updated=updated, wallets=[])
    fx["overview"] = {}          # closed accounts often lose overview data
    fx["cdd"] = {}
    fx["short_info"] = {"id": UUID, "businessName": "ACME GmbH",
                        "status": {"accountStatus": "AccountClosed"}}
    return fx


def test_closed_account_strong_identity_accepted_without_comparable_data():
    # Definitive ticket UUID + nothing comparable in BO -> MATCH (not S4).
    stub = StubBO(fixtures={UUID: _closed_bare_fixture()}, search_map={IBAN: UUID})
    out = match_account(stub, fields())
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "ticket_uuid"
    assert out["status_bucket"] == "CLOSED"
    assert any("identity accepted" in r for r in out["reasons"])


def test_closed_account_name_hit_stays_no_match_without_data():
    # A non-exact NAME hit is weak — without comparable data it must NOT match.
    # (An exact normalized-name hit IS strong — covered separately below.)
    fx = _closed_bare_fixture()
    fx["search_items"] = [{"id": UUID, "businessName": "ACME Handels GmbH",
                           "regNumber": "", "accountStatus": "AccountClosed",
                           "type": ""}]
    f = fields(company_uuid="", seized_iban="", debtor_register_number="")
    stub = StubBO(fixtures={UUID: fx}, search_map={"ACME GmbH": UUID})
    out = match_account(stub, f)
    assert out["outcome"] == "NO_MATCH"


def test_strong_identity_does_not_override_real_mismatch():
    # Comparable data present and mismatching -> still NO_MATCH, even with a
    # definitive ticket UUID.
    fx = company(address={"street": "Elsewhere 9", "zip": "99999", "city": "X"},
                 wallets=[{"id": "w1", "iban": "DE02120300000000202051",
                           "name": "Main", "balance": 1.0, "currency": "EUR"}])
    out = match_account(StubBO(fixtures={UUID: fx}), fields())
    assert out["outcome"] == "NO_MATCH"


def test_unknown_type_with_address_match_confirms():
    fx = company(type_="")          # type lost (closed/onboarding)
    fx["overview"] = {"address": {"street": "Hauptstr. 1", "zip": "60311",
                                  "city": "Frankfurt"}}
    f = fields(seized_iban="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "address"


# --- name-variant search (NOV Energys / double-space class) -----------------------


def test_name_variants_builder():
    from app.matching import name_variants

    assert name_variants("NOV Energys UG (haftungsbeschränkt)") == [
        "NOV Energys UG (haftungsbeschränkt)", "NOV Energys"]
    # BO-style double spaces collapse into a second variant before the base.
    assert name_variants("NOV Energys UG  (haftungsbeschränkt)") == [
        "NOV Energys UG  (haftungsbeschränkt)",
        "NOV Energys UG (haftungsbeschränkt)", "NOV Energys"]
    assert name_variants("Muster GmbH & Co. KG") == ["Muster GmbH & Co. KG", "Muster"]
    assert name_variants("") == []


def test_name_variant_search_with_exact_normalized_name_is_strong():
    # Jira: single space; BO stores a DOUBLE space -> the literal search misses,
    # the suffix-stripped variant hits, and full-name equality (whitespace-
    # collapsed) makes the identity strong enough for a bare closed account.
    bo_name = "NOV Energys UG  (haftungsbeschränkt)"
    fx = company(status="AccountClosed", updated="2026-01-05T00:00:00Z",
                 wallets=[], name=bo_name)
    fx["overview"] = {}
    fx["cdd"] = {}
    fx["short_info"] = {"id": UUID, "businessName": bo_name,
                        "status": {"accountStatus": "AccountClosed"}}
    fx["search_items"] = [{"id": UUID, "businessName": bo_name, "regNumber": "",
                           "accountStatus": "AccountClosed",
                           "accountStatusUpdated": "2026-01-05T00:00:00Z", "type": ""}]
    stub = StubBO(fixtures={UUID: fx},
                  search_items_map={"NOV Energys": fx["search_items"]})
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_name="NOV Energys UG (haftungsbeschränkt)")
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "name"
    assert out["outcome"] == "MATCH"
    assert out["status_bucket"] == "CLOSED"
    assert any("full name equality" in r for r in out["reasons"])
    # The literal query ran first and found nothing; the variant resolved it.
    searched = [t for (c, t) in stub.calls if c == "cstools_search"]
    assert "NOV Energys UG (haftungsbeschränkt)" in searched
    assert "NOV Energys" in searched


def test_name_variant_hit_with_different_name_stays_weak():
    # The variant search returns ONE company but its full name differs from the
    # ticket's -> identified, but weak: a bare closed account stays NO_MATCH.
    bo_name = "NOV Energys Verwaltungs UG (haftungsbeschränkt)"
    fx = company(status="AccountClosed", wallets=[], name=bo_name)
    fx["overview"] = {}
    fx["cdd"] = {}
    fx["short_info"] = {"id": UUID, "businessName": bo_name,
                        "status": {"accountStatus": "AccountClosed"}}
    fx["search_items"] = [{"id": UUID, "businessName": bo_name, "regNumber": "",
                           "accountStatus": "AccountClosed", "type": ""}]
    stub = StubBO(fixtures={UUID: fx},
                  search_items_map={"NOV Energys": fx["search_items"]})
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_name="NOV Energys UG (haftungsbeschränkt)")
    out = match_account(stub, f)
    assert out["outcome"] == "NO_MATCH"


def test_name_variant_multiple_hits_exact_name_wins():
    bo_name = "NOV Energys UG  (haftungsbeschränkt)"
    other = {"id": "99999999-9999-9999-9999-999999999999",
             "businessName": "NOV Energys Consulting GmbH", "regNumber": ""}
    exact = {"id": UUID, "businessName": bo_name, "regNumber": "",
             "accountStatus": "AccountClosed", "type": ""}
    fx = company(status="AccountClosed", name=bo_name)
    stub = StubBO(fixtures={UUID: fx},
                  search_items_map={"NOV Energys": [other, exact]})
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_name="NOV Energys UG (haftungsbeschränkt)")
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert any("full name equality" in r for r in out["reasons"])


# --- IBAN-vs-name conflict (FPOPCL-14753: two sibling legal entities) --------------


UUID_SOLAR = "5a40c513-0000-0000-0000-000000000001"


def _sibling_fixtures():
    """Debtor named 'BS Service GmbH & Co. KG' (application in progress) while
    the ticket's IBAN belongs to sibling 'Solar Solution GmbH & Co. KG'."""
    named = company(uuid=UUID, name="BS Service GmbH & Co. KG",
                    status="ApplicationInProgress", wallets=[])
    named["overview"] = {}
    named["cdd"] = {}
    named["short_info"] = {"id": UUID, "businessName": "BS Service GmbH & Co. KG",
                           "status": {"accountStatus": "ApplicationInProgress"}}
    sibling = company(uuid=UUID_SOLAR, name="Solar Solution GmbH & Co. KG",
                      status="AccountBlocked")
    sibling["search_items"][0]["iban"] = IBAN
    return named, sibling


def test_iban_name_conflict_surfaces_both_candidates():
    named, sibling = _sibling_fixtures()
    stub = StubBO(
        fixtures={UUID: named, UUID_SOLAR: sibling},
        search_items_map={
            IBAN: sibling["search_items"],
            "BS Service GmbH & Co. KG": named["search_items"],
        },
    )
    f = fields(company_uuid="", debtor_register_number="",
               debtor_name="BS Service GmbH & Co. KG")
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert out["outcome"] is None
    ids = [c["id"] for c in out["candidates"]]
    assert UUID_SOLAR in ids and UUID in ids
    assert "different legal entity" in " ".join(out["reasons"]) or "belongs to" in (out["error"] or "")


def test_iban_name_conflict_operator_pick_resolves_s3():
    named, sibling = _sibling_fixtures()
    stub = StubBO(
        fixtures={UUID: named, UUID_SOLAR: sibling},
        search_items_map={
            IBAN: sibling["search_items"],
            "BS Service GmbH & Co. KG": named["search_items"],
        },
    )
    f = fields(company_uuid="", debtor_register_number="",
               debtor_name="BS Service GmbH & Co. KG")
    # Operator picks the NAMED debtor -> onboarding -> S3 territory.
    out = match_account(stub, f, manual_uuid=UUID)
    assert out["outcome"] == "MATCH"          # strong identity, no comparable data
    assert out["status_bucket"] == "ONBOARDING"


def test_iban_hit_with_matching_name_unaffected():
    # Normal case: the IBAN's account carries the debtor's name -> no conflict.
    stub = StubBO(fixtures={UUID: company()}, search_map={IBAN: UUID})
    f = fields(company_uuid="", debtor_register_number="")
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "iban"
    assert out["needs_selection"] is False


def test_iban_name_mismatch_without_name_hit_proceeds_with_warning():
    # The debtor name resolves nowhere else -> keep the IBAN company but warn.
    sibling = company(uuid=UUID, name="Solar Solution GmbH & Co. KG")
    stub = StubBO(fixtures={UUID: sibling}, search_map={IBAN: UUID})
    f = fields(company_uuid="", debtor_register_number="",
               debtor_name="BS Service GmbH & Co. KG")
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert any("does not match the ticket's debtor name" in r for r in out["reasons"])
