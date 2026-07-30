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
