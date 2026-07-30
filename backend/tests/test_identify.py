"""Identification tests (Step 1) against ``app.matching.match_account``.

Ported from mini's identify suite: manual UUID, ticket UUID, multi-UUID
wallet-IBAN disambiguation, search order (register number -> IBAN -> name),
candidate surfacing, and BO failure behavior.
"""
from __future__ import annotations

import pytest

from app.bo_client import BOError
from app.matching import match_account
from tests.fixtures import IBAN, IBAN2, UUID, UUID2, company, fields
from tests.stub_bo import StubBO


def _two_company_fixtures():
    return {
        UUID: company(uuid=UUID, name="ACME GmbH"),
        UUID2: company(uuid=UUID2, name="Other GmbH",
                       wallets=[{"id": "w2", "iban": IBAN2, "name": "Main",
                                 "balance": 1.0, "currency": "EUR"}]),
    }


def test_manual_uuid_wins_and_fetches_name():
    stub = StubBO(fixtures={UUID: company()})
    out = match_account(stub, fields(company_uuid=""), manual_uuid=UUID)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "manual"
    assert out["business_name"] == "ACME GmbH"
    assert out["needs_selection"] is False


def test_ticket_uuid_path():
    stub = StubBO(fixtures={UUID: company()})
    out = match_account(stub, fields())
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "ticket_uuid"
    assert out["outcome"] == "MATCH"          # IBAN confirmation
    assert out["matched_by"] == "iban"


def test_candidates_resolved_by_seized_iban_wallet_owner():
    f = fields(company_uuid="", company_uuid_candidates=f"{UUID}, {UUID2}")
    stub = StubBO(fixtures=_two_company_fixtures())
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID        # owns the seized IBAN
    assert out["identified_by"] == "wallet_iban"
    assert out["needs_selection"] is False


def test_candidates_without_ticket_iban_need_selection():
    f = fields(company_uuid="", seized_iban="",
               company_uuid_candidates=f"{UUID}, {UUID2}")
    stub = StubBO(fixtures=_two_company_fixtures())
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert out["outcome"] is None
    assert [c["id"] for c in out["candidates"]] == [UUID, UUID2]
    assert "no seized/debtor IBAN" in out["error"]


def test_candidates_no_wallet_owner_needs_selection():
    f = fields(company_uuid="", seized_iban="DE75512108001245126199",
               company_uuid_candidates=f"{UUID}, {UUID2}")
    stub = StubBO(fixtures=_two_company_fixtures())
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert "no candidate company's wallets carry" in out["error"]


def test_candidates_ambiguous_owners_need_selection():
    fx = _two_company_fixtures()
    fx[UUID2]["wallets"] = [{"id": "w2", "iban": IBAN, "name": "Main",
                             "balance": 1.0, "currency": "EUR"}]  # same IBAN
    f = fields(company_uuid="", company_uuid_candidates=f"{UUID}, {UUID2}")
    out = match_account(StubBO(fixtures=fx), f)
    assert out["needs_selection"] is True
    assert "several candidate companies own" in out["error"]


def test_candidates_wallet_error_on_one_still_resolves_other():
    class OneFailStub(StubBO):
        def wallets(self, company_uuid):
            if company_uuid == UUID2:
                raise BOError("wallets", 502, "down")
            return super().wallets(company_uuid)

    f = fields(company_uuid="", company_uuid_candidates=f"{UUID}, {UUID2}")
    out = match_account(OneFailStub(fixtures=_two_company_fixtures()), f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "wallet_iban"


def test_register_number_unique_hit():
    f = fields(company_uuid="")
    stub = StubBO(fixtures={UUID: company()}, search_map={"HRB12345": UUID})
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "register_number"


def test_iban_hit_after_empty_register():
    f = fields(company_uuid="", debtor_register_number="")
    stub = StubBO(fixtures={UUID: company()}, search_map={IBAN: UUID})
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "iban"


def test_name_hit_last():
    f = fields(company_uuid="", debtor_register_number="", seized_iban="")
    stub = StubBO(fixtures={UUID: company()}, search_map={"ACME GmbH": UUID})
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "name"


def test_multiple_search_hits_exact_reg_match_wins():
    items = [
        {"id": UUID2, "businessName": "Lookalike", "regNumber": "HRB 99999"},
        {"id": UUID, "businessName": "ACME GmbH", "regNumber": "HRB 12345"},
    ]
    f = fields(company_uuid="")
    stub = StubBO(fixtures={UUID: company()},
                  search_items_map={"HRB12345": items})
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "register_number"


def test_multiple_inexact_hits_need_selection():
    # Neither candidate's FULL name equals the ticket's -> operator picks.
    items = [
        {"id": UUID, "businessName": "ACME Trading GmbH", "regNumber": ""},
        {"id": UUID2, "businessName": "ACME Holding GmbH", "regNumber": ""},
    ]
    f = fields(company_uuid="", debtor_register_number="", seized_iban="")
    stub = StubBO(search_items_map={"ACME GmbH": items})
    out = match_account(stub, f)
    assert out["needs_selection"] is True
    assert out["outcome"] is None
    assert len(out["candidates"]) == 2


def test_nothing_resolved_is_no_match():
    f = fields(company_uuid="", debtor_register_number="", seized_iban="",
               debtor_name="Unknown Ltd")
    out = match_account(StubBO(), f)
    assert out["needs_selection"] is False
    assert out["outcome"] == "NO_MATCH"
    assert out["company_uuid"] == ""


def test_boerror_during_search_propagates():
    f = fields(company_uuid="")
    stub = StubBO(fail={"cstools_search"})
    with pytest.raises(BOError):
        match_account(stub, f)


def test_resolved_uuid_reuses_wallets_for_balance():
    stub = StubBO(fixtures={UUID: company()})
    out = match_account(stub, fields())
    assert [w["iban"] for w in out["wallets_items"]] == [IBAN]
    # exactly one wallets call for the resolved company (no duplicate fetch)
    assert [c for c in stub.calls if c[0] == "wallets"] == [("wallets", UUID)]


def test_multiple_hits_exact_name_auto_resolves():
    # One of several candidates carries the ticket's exact (normalized) name
    # -> it wins without operator selection.
    items = [
        {"id": UUID2, "businessName": "ACME Holding GmbH", "regNumber": ""},
        {"id": UUID, "businessName": "ACME GmbH", "regNumber": ""},
    ]
    f = fields(company_uuid="", debtor_register_number="", seized_iban="")
    stub = StubBO(fixtures={UUID: company()}, search_items_map={"ACME GmbH": items})
    out = match_account(stub, f)
    assert out["company_uuid"] == UUID
    assert out["identified_by"] == "name"
    assert out["needs_selection"] is False
