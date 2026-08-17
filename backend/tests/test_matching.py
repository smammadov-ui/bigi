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
    # Physical person (DOB present, no register number, person name) matched
    # to a Company account.
    f = fields(debtor_dob="1980-05-05", debtor_register_number="",
               debtor_name="Hamza Bosnjak")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["outcome"] == "PERSON_VS_COMPANY"


def test_physical_person_heuristic():
    assert is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": ""})
    assert not is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": "HRB 1"})
    assert not is_physical_person({"debtor_dob": "", "debtor_register_number": ""})
    # FPOPCL-31103: a legal form in the debtor name vetoes a stray DOB.
    assert not is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": "",
                                   "debtor_name": "Magcars UG (haftungsbeschränkt)"})
    assert not is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": "",
                                   "debtor_name": "Muster GmbH & Co. KG"})
    assert is_physical_person({"debtor_dob": "1980-05-05", "debtor_register_number": "",
                               "debtor_name": "Hamza Bosnjak"})


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


# --- graded address comparison (FPOPCL-30939 class) --------------------------------


def test_same_postcode_different_street_is_no_match():
    # Doppelgänger with the SAME postcode: postcode-only used to bless this.
    fx = company(address={"street": "Gutleutstraße", "houseNo": "99",
                          "postCode": "60311", "city": "Frankfurt"})
    f = fields(seized_iban="", debtor_address="Musterweg 5, 60311, , Frankfurt")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "NO_MATCH"
    assert any("different street" in r for r in out["reasons"])
    assert out["address_check"]["grade"] == "mismatch"


def test_weak_address_with_key_identity_matches():
    # Postcode agrees, street missing in BO; identity via definitive ticket UUID.
    fx = company(address={"postCode": "60311", "city": "Frankfurt"})
    f = fields(seized_iban="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "address"
    assert any("weak address evidence accepted" in r for r in out["reasons"])


def test_weak_address_with_name_identity_goes_to_picker():
    fx = company(address={"postCode": "60311", "city": "Frankfurt"})
    f = fields(company_uuid="", seized_iban="", debtor_register_number="")
    stub = StubBO(fixtures={UUID: fx}, search_map={"ACME GmbH": UUID})
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert [c["id"] for c in out["candidates"]] == [UUID]
    assert "partially matches" in (out["error"] or "")


def test_exact_name_open_account_without_data_goes_to_picker():
    # OPEN account with nothing to compare + exact-name identity -> operator.
    fx = company(wallets=[])
    fx["overview"] = {}
    fx["cdd"] = {}
    f = fields(company_uuid="", seized_iban="", debtor_register_number="")
    stub = StubBO(fixtures={UUID: fx}, search_map={"ACME GmbH": UUID})
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert "no address/IBAN/DOB to verify" in (out["error"] or "")


def test_exact_name_closed_account_without_data_still_accepted():
    # Regression: NOV Energys class must keep working (CLOSED bucket).
    fx = company(status="AccountClosed", updated="2026-01-05T00:00:00Z", wallets=[])
    fx["overview"] = {}
    fx["cdd"] = {}
    fx["short_info"] = {"id": UUID, "businessName": "ACME GmbH",
                        "status": {"accountStatus": "AccountClosed"}}
    f = fields(company_uuid="", seized_iban="", debtor_register_number="")
    stub = StubBO(fixtures={UUID: fx}, search_map={"ACME GmbH": UUID})
    out = match_account(stub, f)
    assert out["outcome"] == "MATCH"
    assert out["status_bucket"] == "CLOSED"


def test_main_wallet_prefers_eur():
    fx = company(wallets=[
        {"id": "w1", "iban": "US00USD", "name": "USD", "balance": 1.0, "currency": "USD"},
        {"id": "w2", "iban": "DE00EUR", "name": "Zweit", "balance": 1.0, "currency": "EUR"},
    ])
    f = fields(seized_iban="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["seized_iban"] == "DE00EUR"
    assert out["main_wallet"]["iban"] == "DE00EUR"


def test_main_wallet_prefers_de_eur_over_gb_eur():
    # Live case: first EUR wallet carried a GB IBAN — the letter must quote DE.
    fx = company(wallets=[
        {"id": "w1", "iban": "GB27TCCL04140487724945", "name": "EUR",
         "balance": 0.0, "currency": "EUR"},
        {"id": "w2", "iban": "DE08100180000526392909", "name": "Zweit",
         "balance": 27363.65, "currency": "EUR"},
    ])
    f = fields(seized_iban="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["seized_iban"] == "DE08100180000526392909"


# --- same-name disambiguation by address (FPOPCL-31056 class) ----------------------


def _elbstar_items(n=4, reg="HRB 180002"):
    names = ["Elbstar Solution GmbH", "ELBSTAR SOLUTION GmbH",
             "ELBSTAR SOLUTION GmbH", "ELBSTAR SOLUTION Gmbh"]
    return [{"id": f"e1b57a12-000{i}-4000-8000-00000000000{i}",
             "businessName": names[i], "regNumber": reg,
             "accountStatus": "AccountOpened", "type": "Company"}
            for i in range(n)]


def _stub_with_overviews(items, addr_by_id, extra_fixtures=None):
    fx = dict(extra_fixtures or {})
    for it in items:
        f = company(uuid=it["id"], name=it["businessName"])
        f["overview"] = {"type": "Company", "address": addr_by_id.get(it["id"], {})}
        fx[it["id"]] = f
    noise = [{"id": "00000000-9999-4000-8000-000000000009",
              "businessName": "SC Solution GmbH", "regNumber": ""}]
    return StubBO(fixtures=fx, search_items_map={"ELBSTAR SOLUTION GmbH": items + noise,
                                                 "ELBSTAR SOLUTION": items + noise})


HAMBURG = {"street": "Am Hehsel", "houseNo": "38", "postCode": "22339", "city": "Hamburg"}
ELSEWHERE = {"street": "Andere Str.", "houseNo": "1", "postCode": "99999", "city": "X"}
TICKET_ADDR = "Am Hehsel 38, , 22339, Hamburg"


def _elbstar_fields():
    return fields(company_uuid="", seized_iban="", debtor_register_number="",
                  debtor_name="ELBSTAR SOLUTION GmbH", debtor_address=TICKET_ADDR)


def test_same_name_disambiguated_by_single_strong_address():
    items = _elbstar_items()
    addr = {items[0]["id"]: ELSEWHERE, items[1]["id"]: HAMBURG,
            items[2]["id"]: ELSEWHERE, items[3]["id"]: ELSEWHERE}
    out = match_account(_stub_with_overviews(items, addr), _elbstar_fields())
    assert out["company_uuid"] == items[1]["id"]
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "address"
    assert any("disambiguated by address" in r for r in out["reasons"])


def test_same_name_duplicates_prefer_open_account():
    items = _elbstar_items()
    items[1]["accountStatus"] = "AccountClosed"
    items[2]["accountStatus"] = "AccountOpened"
    items[3]["accountStatus"] = "AccountClosed"
    items[0]["accountStatus"] = "AccountClosed"
    addr = {it["id"]: HAMBURG for it in items}   # true duplicates: same address
    out = match_account(_stub_with_overviews(items, addr), _elbstar_fields())
    assert out["company_uuid"] == items[2]["id"]
    assert any("OPEN account chosen" in r for r in out["reasons"])


def test_same_name_multiple_open_strong_goes_to_annotated_picker():
    items = _elbstar_items()
    addr = {it["id"]: HAMBURG for it in items}   # all strong, all open
    out = match_account(_stub_with_overviews(items, addr), _elbstar_fields())
    assert out["needs_selection"] is True
    assert len(out["candidates"]) == 4           # exact subset only, not the noise
    assert all("address: strong" in (c.get("note") or "") for c in out["candidates"])


def test_same_name_none_strong_annotated_picker():
    items = _elbstar_items()
    addr = {it["id"]: ELSEWHERE for it in items}
    out = match_account(_stub_with_overviews(items, addr), _elbstar_fields())
    assert out["needs_selection"] is True
    assert all("address: mismatch" in (c.get("note") or "") for c in out["candidates"])


def test_same_name_without_ticket_address_plain_picker():
    items = _elbstar_items()
    f = _elbstar_fields()
    f["debtor_address"] = ""
    out = match_account(_stub_with_overviews(items, {}), f)
    assert out["needs_selection"] is True
    assert len(out["candidates"]) == 4


def test_fuzzy_noise_capped_at_12():
    noise = [{"id": f"00000000-0000-4000-8000-0000000000{i:02d}",
              "businessName": f"Solution {i} GmbH", "regNumber": ""} for i in range(30)]
    stub = StubBO(search_items_map={"Unique Name GmbH": noise, "Unique Name": noise})
    f = fields(company_uuid="", seized_iban="", debtor_register_number="",
               debtor_name="Unique Name GmbH")
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert len(out["candidates"]) == 12
    assert "first 12 of 30" in (out["error"] or "")


def test_same_name_floor_prefix_still_disambiguates():
    # FPOPCL-31056: the right record's BO street carries a floor prefix.
    items = _elbstar_items()
    addr = {items[0]["id"]: {},                              # onboarding shell, no address
            items[1]["id"]: {"street": "II OG, Am Hehsel 38",
                             "postCode": "22339", "city": "Hamburg"},
            items[2]["id"]: ELSEWHERE, items[3]["id"]: ELSEWHERE}
    out = match_account(_stub_with_overviews(items, addr), _elbstar_fields())
    assert out["company_uuid"] == items[1]["id"]
    assert out["outcome"] == "MATCH"


def test_annotated_picker_sorted_by_grade():
    items = _elbstar_items()
    addr = {items[0]["id"]: ELSEWHERE,                       # mismatch
            items[1]["id"]: {},                              # unknown
            items[2]["id"]: {"postCode": "22339"},           # weak (street missing)
            items[3]["id"]: {"postCode": "22339"}}           # weak
    out = match_account(_stub_with_overviews(items, addr), _elbstar_fields())
    assert out["needs_selection"] is True
    notes = [c["note"] for c in out["candidates"]]
    assert notes[0].startswith("address: weak")
    assert notes[-1].startswith("address: mismatch")


# --- items 5/6/7 (FPOPCL-31102 + analyst identification matrix) ---------------


def test_dob_matches_across_formats():
    # Ticket says 1989-08-21, BO's CDD stores 21.08.1989 — same birthday.
    fx = company(type_="Freelancer", name="Susann Piekorz", dob="21.08.1989",
                 address={"street": "Am Bahnhofsvorplatz 7", "zip": "02977",
                          "city": "Hoyerswerda"})
    f = fields(seized_iban="", debtor_dob="1989-08-21", debtor_register_number="",
               debtor_name="Susann Piekorz",
               debtor_address="Gewerbepark 35a, 02997 Wittichenau")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "dob"


def test_iban_match_requires_name_agreement():
    # Analyst matrix: IBAN alone is NOT definitive — the name must agree too.
    f = fields(debtor_name="Voellig Andere Handels GmbH")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["outcome"] == "NO_MATCH"
    assert any("not definitive per the identification rules" in r
               for r in out["reasons"])


def test_iban_match_tolerates_trade_name_extension():
    # "Freelancer register name (a bit different from the main name)" counts.
    fx = company(type_="Freelancer", name="Susann Piekorz")
    f = fields(debtor_name="Malerbetrieb Susann Piekorz",
               debtor_register_number="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "iban"


def test_conflicting_legal_forms_never_agree():
    # ACME UG and ACME GmbH are different legal entities.
    f = fields(debtor_name="ACME UG")
    out = match_account(StubBO(fixtures={UUID: company()}), f)   # ACME GmbH
    assert out["outcome"] == "NO_MATCH"


def test_registered_trade_name_from_cdd_counts():
    fx = company(name="SP Design")
    fx["cdd"]["CompanyRegisteredName"] = "Susann Piekorz Grafikdesign"
    f = fields(seized_iban="", debtor_name="Susann Piekorz Grafikdesign",
               debtor_register_number="")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"          # name (via CDD) + address
    assert out["matched_by"] == "address"


def test_non_company_candidate_uuids_are_dropped():
    # FPOPCL-31102: the seizure link's UUID 404s as a company -> dropped; the
    # single survivor resolves WITHOUT the picker.
    bogus = "44444444-4444-4444-4444-444444444444"
    f = fields(company_uuid="", seized_iban="",
               company_uuid_candidates=f"{UUID}, {bogus}")
    out = match_account(StubBO(fixtures={UUID: company()}), f)
    assert out["needs_selection"] is False
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "ticket_uuid"
    assert out["outcome"] == "MATCH"
    assert any("not" in r and bogus in r for r in out["reasons"])


def test_all_invalid_candidates_fall_through_to_search(monkeypatch):
    # B11: when EVERY ticket UUID 404s as a company, the dropped UUIDs must NOT
    # be resurrected as picker candidates — identification falls through to the
    # register/IBAN/name search instead.
    b1 = "44444444-4444-4444-4444-444444444444"
    b2 = "55555555-5555-5555-5555-555555555555"
    fx = company()   # the real debtor, found by IBAN search below
    stub = StubBO(fixtures={UUID: fx}, search_map={IBAN: UUID})
    f = fields(company_uuid="", company_uuid_candidates=f"{b1}, {b2}")  # seized IBAN present
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID          # resolved via search, not the picker
    assert out["needs_selection"] is False
    assert b1 not in [c.get("id") for c in (out.get("candidates") or [])]
    assert any("no ticket UUID resolved to a BO company" in r for r in out["reasons"])


def test_seized_iban_source_from_debtor_list(monkeypatch, db, client):
    # B13: an IBAN taken from the debtor list is labelled "debtor_list", not
    # "provided". Threaded parser -> pipeline -> matching.
    from app import pipeline
    from tests.fixtures import raw_ticket
    fx = company()
    stub = StubBO(fixtures={UUID: fx})
    monkeypatch.setattr(pipeline, "BOClient", lambda *a, **k: stub)
    f = fields(seized_iban="", debtor_ibans=IBAN)      # no seized field; one debtor IBAN
    r = pipeline.run_pipeline(db, raw_ticket(f))
    assert r["account"]["seized_iban"] == IBAN
    assert r["account"]["seized_iban_source"] == "debtor_list"


def test_name_similarity_units():
    from app.matching import _legal_form_key, _names_similar

    assert _legal_form_key("Magcars UG (haftungsbeschränkt)") == "ug"
    assert _legal_form_key("ACME gGmbH") == "gmbh"
    assert _legal_form_key("ACME mbH") == "gmbh"
    assert _legal_form_key("Susann Piekorz") == ""
    assert _names_similar("Magcars UG (haftungsbeschränkt)", "Magcars UG")
    assert _names_similar("Müller Bäckerei GmbH", "Baeckerei Mueller GmbH")
    assert _names_similar("ACME GmbH", "ACME  GmbH")
    assert not _names_similar("ACME UG", "ACME GmbH")
    assert not _names_similar("Hamza Bosnjak", "ACME GmbH")
    assert _names_similar("Susann Piekorz", "Malerbetrieb Susann Piekorz")


def test_freelancer_trade_name_confirms_via_person_full_name():
    # FPOPCL-31366: sole trader — businessName is the TRADE name, the seizure
    # names the person. PersonFullName from the CDD must count.
    fx = company(type_="Freelancer", name="HLP Druck - Textilveredelung",
                 address={"street": "Hermann-Oberth-Str. 5", "zip": "83052",
                          "city": "Bruckmühl"})
    fx["cdd"]["PersonFullName"] = "Tarkan Öztepe"
    f = fields(seized_iban="", debtor_register_number="",
               debtor_name="Tarkan Öztepe", debtor_dob="1975-01-01",
               debtor_address="Hermann-Oberth-Str. 5, 83052 Bruckmühl")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "MATCH"
    assert out["matched_by"] == "address"


def test_person_full_name_never_counts_for_companies():
    # A director's name is NOT the company: gate stays blocked -> S5 handles it.
    fx = company()   # Company "ACME GmbH"
    fx["cdd"]["PersonFullName"] = "Hamza Bosnjak"
    f = fields(debtor_dob="1980-05-05", debtor_register_number="",
               debtor_name="Hamza Bosnjak")
    out = match_account(StubBO(fixtures={UUID: fx}), f)
    assert out["outcome"] == "PERSON_VS_COMPANY"


def test_cdd_person_names_nested_and_deduped():
    from app.matching import _cdd_person_names

    cdd = {"sections": [{"parameters": [
        {"parameter": "PersonFullName", "values": [{"value": "Tarkan Öztepe"}]},
        {"parameter": "PersonFullName", "values": [{"value": "tarkan  öztepe"}]},
        {"parameter": "PersonEmail", "values": [{"value": "x@y.z"}]},
    ]}]}
    assert _cdd_person_names(cdd) == ["Tarkan Öztepe"]
    assert _cdd_person_names({"PersonFullName": "Susann Piekorz"}) == ["Susann Piekorz"]
    assert _cdd_person_names({}) == []
