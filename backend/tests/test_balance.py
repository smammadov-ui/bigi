"""Offline tests for the EUR-only balance + held-funds helpers (app.checks)."""
from __future__ import annotations

from app.checks import account_balance, held_funds


def test_error_passthrough():
    out = account_balance(None, error="account not resolved — balance check skipped")
    assert out["available_eur"] is None
    assert "account not resolved" in out["error"]
    assert out["breakdown"] == [] and out["non_eur"] == []


def test_eur_only_sum():
    items = [{"iban": "DE1", "name": "Main", "balance": 6771.29, "currency": "EUR"}]
    out = account_balance(items)
    assert out["available_eur"] == 6771.29
    assert out["available_eur_de"] == "6.771,29"
    assert out["error"] is None
    assert len(out["breakdown"]) == 1


def test_multiple_eur_wallets_summed():
    items = [
        {"iban": "DE1", "name": "Main", "balance": 100.10, "currency": "EUR"},
        {"iban": "DE2", "name": "Sub", "balance": 200.15, "currency": "EUR"},
    ]
    out = account_balance(items)
    assert out["available_eur"] == 300.25


def test_non_eur_wallets_excluded_not_converted():
    items = [
        {"iban": "DE1", "name": "Main", "balance": 50.0, "currency": "EUR"},
        {"iban": "GB1", "name": "GBP", "balance": 999.0, "currency": "GBP"},
    ]
    out = account_balance(items)
    assert out["available_eur"] == 50.0
    assert [w["currency"] for w in out["non_eur"]] == ["GBP"]


def test_missing_currency_defaults_to_eur():
    out = account_balance([{"iban": "DE1", "balance": 10}])
    assert out["available_eur"] == 10.0


def test_garbage_balance_treated_as_zero():
    out = account_balance([{"iban": "DE1", "balance": "n/a", "currency": "EUR"}])
    assert out["available_eur"] == 0.0


def test_held_funds_sums_own_case_too():
    sc = {
        "seizures": [{"seized_amount": 100.5, "client_total": None}],
        "ignored_same_case": [{"seized_amount": 200.0, "client_total": 42.0}],
    }
    out = held_funds(sc)
    assert out["held_eur"] == 300.5
    assert out["held_eur_de"] == "300,50"
    assert out["client_total_eur"] == 42.0
    assert out["client_total_eur_de"] == "42,00"


def test_held_funds_empty():
    out = held_funds({"seizures": [], "ignored_same_case": []})
    assert out["held_eur"] == 0.0
    assert out["held_eur_de"] is None
    assert out["client_total_eur"] is None
