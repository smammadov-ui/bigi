"""Shared test fixtures — importable without executing any tests."""
from __future__ import annotations

UUID = "11111111-1111-1111-1111-111111111111"
UUID2 = "22222222-2222-2222-2222-222222222222"
IBAN = "DE89370400440532013000"
IBAN2 = "DE02120300000000202051"
CASE_REF = "2614/239/24045 - VO 05"


def company(status="AccountOpened", *, uuid=UUID, name="ACME GmbH", wallets=None,
            alerts=None, seizures=None, details=None,
            updated="2026-01-10T00:00:00Z", type_="Company",
            address=None, dob="1980-05-05"):
    return {
        "search_items": [{"id": uuid, "businessName": name, "regNumber": "HRB 12345",
                          "iban": IBAN, "accountStatus": status,
                          "accountStatusUpdated": updated, "type": type_}],
        "short_info": {"id": uuid, "businessName": name,
                       "status": {"accountStatus": status},
                       "accountStatusUpdated": updated, "type": type_},
        "overview": {"type": type_, "address": address if address is not None else {
            "street": "Hauptstr. 1", "zip": "60311", "city": "Frankfurt"}},
        "cdd": {"PersonBirthdate": dob},
        "wallets": wallets if wallets is not None else [
            {"id": "w1", "iban": IBAN, "name": "Main", "balance": 5000.0, "currency": "EUR"}],
        "alerts": alerts or [],
        "seizures": seizures or [],
        "seizure_details": details or {},
    }


def fields(**over):
    base = {
        "company_uuid": UUID,
        "seized_iban": IBAN,
        "seizure_amount": "3000.00",
        "date_received": "2026-02-01",
        "issued_date": "2026-01-20",
        "debtor_name": "ACME GmbH",
        "debtor_address": "Hauptstr. 1, 60311 Frankfurt",
        "debtor_register_number": "HRB 12345",
        "case_references": CASE_REF,
        "creditor_name": "Finanzamt Bremen",
        "creditor_address": "Amtsweg 2, 28195 Bremen",
    }
    base.update(over)
    return base


def raw_ticket(f: dict | None = None) -> str:
    """Build a parseable Jira ticket text from a ``fields()`` dict.

    Only keys the parser recognises are emitted; the creditor name/issued date
    travel in the prose line, mirroring real tickets.
    """
    f = dict(fields()) if f is None else dict(f)
    lines = []
    creditor = f.get("creditor_name") or "Finanzamt Bremen"
    issued = f.get("issued_date") or "2026-01-20"
    lines.append(f"We received a seizure request from {creditor} issued on {issued}.")
    keymap = [
        ("seizure type", "seizure_type"),
        ("seizure amount", "seizure_amount"),
        ("document type", "document_type"),
        ("date received", "date_received"),
        ("debtor name", "debtor_name"),
        ("debtor address", "debtor_address"),
        ("debtor date of birth", "debtor_dob"),
        ("debtor register number", "debtor_register_number"),
        ("debtor list of IBANs", "debtor_ibans"),
        ("case references", "case_references"),
        ("creditor address", "creditor_address"),
        ("creditor IBAN", "creditor_iban"),
        ("creditor BIC", "creditor_bic"),
        ("creditor email", "creditor_email"),
    ]
    for label, key in keymap:
        val = f.get(key)
        if val:
            lines.append(f"* {label}: {val}")
    if f.get("seized_iban"):
        lines.append(f"* seized IBANs: {f['seized_iban']}")
    if f.get("company_uuid"):
        lines.append(f"* definitive match: {f['company_uuid']}")
    elif f.get("company_uuid_candidates"):
        lines.append(f"* definitive match: {f['company_uuid_candidates']}")
    return "\n".join(lines) + "\n"
