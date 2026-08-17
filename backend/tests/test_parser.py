"""Tests for app.parser — deterministic Jira parser."""
from app.matching import norm_reg
from app.parser import PARSED_FIELDS, empty_parsed, parse_jira


SAMPLE_TICKET = """\
Seizure request from Finanzamt Berlin issued on 03.02.2026.

Definitive match: 11111111-2222-3333-4444-555555555555
Seizure type: Kontopfändung
Seizure amount: 14,250.00 EUR
Document type: Pfändungs- und Überweisungsbeschluss
Date received: 05.02.2026
Debtor name: Muster GmbH
Debtor address: Hauptstraße 1, 10115 Berlin
Case references: AZ 12345/26
Creditor bank: Bundesbank
Creditor iban: DE89370400440532013000
Creditor bic: MARKDEF1100
Creditor address: Finanzamtstr. 9, 10115 Berlin
Creditor email: kasse@fa-berlin.de
Seized IBANs: DE44500105175407324931
Debtor register number: HRB 990011
Debtor tax id: 29/123/45678
"""


def test_empty_parsed_has_all_keys():
    ep = empty_parsed()
    assert set(ep) == set(PARSED_FIELDS)
    assert all(v == "" for v in ep.values())


def test_sample_ticket_core_fields():
    res = parse_jira(SAMPLE_TICKET)
    f = res["fields"]
    assert f["creditor_name"] == "Finanzamt Berlin"
    assert f["debtor_name"] == "Muster GmbH"
    assert f["case_references"] == "AZ 12345/26"
    assert f["seizure_type"] == "Kontopfändung"


def test_sample_amount_normalized():
    res = parse_jira(SAMPLE_TICKET)
    # 14,250.00 EUR (US format) -> normalized to two-decimal string
    assert res["fields"]["seizure_amount"] == "14250.00"


def test_sample_dates_iso():
    res = parse_jira(SAMPLE_TICKET)
    assert res["fields"]["date_received"] == "2026-02-05"
    assert res["fields"]["issued_date"] == "2026-02-03"


def test_sample_company_uuid_from_definitive_match():
    res = parse_jira(SAMPLE_TICKET)
    assert res["fields"]["company_uuid"] == "11111111-2222-3333-4444-555555555555"


def test_sample_register_number_captured():
    res = parse_jira(SAMPLE_TICKET)
    assert res["fields"]["debtor_register_number"] == "HRB 990011"


def test_sample_seized_iban_provided():
    res = parse_jira(SAMPLE_TICKET)
    assert res["fields"]["seized_iban"] == "DE44500105175407324931"
    assert res["seized_iban_source"] == "provided"
    assert res["halted"] is False


def test_comment_is_leading_prose():
    res = parse_jira(SAMPLE_TICKET)
    assert "Seizure request from Finanzamt Berlin" in res["fields"]["comment"]
    # the comment is only the prose before the first field line
    assert "Seizure amount" not in res["fields"]["comment"]


# --- company_uuid: absent is a WARNING, not a halt -------------------------

def test_no_uuid_is_warning_not_halt():
    text = (
        "Seizure type: Kontopfändung\n"
        "Seizure amount: 1.000,00\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == ""
    assert res["halted"] is False
    assert any("company_uuid" in w for w in res["warnings"])


def test_potential_match_fallback():
    text = (
        "Potential match: 22222222-3333-4444-5555-666666666666\n"
        "Seizure amount: 100,00\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == "22222222-3333-4444-5555-666666666666"


# --- company_uuid: several / malformed UUIDs -------------------------------

UUID_A = "619e0724-3dcb-47e6-911b-58c9f7fc2dc8"
UUID_B = "b8af2952-86b7-484a-8e72-6948d910ec9e"


def test_two_uuids_in_definitive_match_become_candidates():
    # The FPOPCL-27769 shape: one field listing two comma-separated UUIDs. The
    # joined string must NOT land in company_uuid (it 400s every BO endpoint).
    text = (
        f"Definitive match: {UUID_A}, {UUID_B}\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == ""
    assert res["fields"]["company_uuid_candidates"] == f"{UUID_A}, {UUID_B}"
    assert res["halted"] is False
    assert any("multiple company UUIDs" in w for w in res["warnings"])


def test_definitive_and_potential_both_filled_become_candidates():
    text = (
        f"Definitive match: {UUID_A}\n"
        f"Potential match: {UUID_B}\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == ""
    assert res["fields"]["company_uuid_candidates"] == f"{UUID_A}, {UUID_B}"
    assert any("multiple company UUIDs" in w for w in res["warnings"])


def test_same_uuid_in_both_fields_resolves_single():
    text = (
        f"Definitive match: {UUID_A}\n"
        f"Potential match: {UUID_A.upper()}\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == UUID_A
    assert res["fields"]["company_uuid_candidates"] == ""


def test_malformed_uuid_dropped_with_warning():
    text = (
        "Definitive match: def-uuid\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == ""
    assert res["fields"]["company_uuid_candidates"] == ""
    assert res["halted"] is False
    assert any("malformed company UUID" in w for w in res["warnings"])


# --- seized_iban halts -----------------------------------------------------

def test_masked_seized_iban_halts():
    text = (
        "Definitive match: u\n"
        "Seized IBANs: DE44 **** **** 4931\n"
    )
    res = parse_jira(text)
    assert res["halted"] is True
    assert any("seized_iban invalid" in r for r in res["halt_reasons"])
    assert res["fields"]["seized_iban"] == ""


def test_multiple_debtor_ibans_halts():
    text = (
        "Definitive match: u\n"
        "Debtor list of IBANs: DE44500105175407324931, DE89370400440532013000\n"
    )
    res = parse_jira(text)
    assert res["halted"] is True
    assert any("multiple debtor IBANs" in r for r in res["halt_reasons"])


def test_single_debtor_iban_used_as_seized():
    text = (
        "Definitive match: u\n"
        "Debtor list of IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["seized_iban"] == "DE44500105175407324931"
    assert res["seized_iban_source"] == "debtor_list"
    assert res["halted"] is False


def test_absent_seized_iban_is_warning():
    text = (
        "Definitive match: u\n"
        "Seizure amount: 100,00\n"
    )
    res = parse_jira(text)
    assert res["fields"]["seized_iban"] == ""
    assert res["halted"] is False
    assert any("seized_iban not provided" in w for w in res["warnings"])


# --- amount halts ----------------------------------------------------------

def test_unparseable_amount_halts():
    text = (
        "Definitive match: u\n"
        "Seizure amount: lots of money\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["halted"] is True
    assert any("seizure_amount unparseable" in r for r in res["halt_reasons"])
    assert res["fields"]["seizure_amount"] == ""


# --- bullets / continuation / noise ---------------------------------------

def test_optional_bullets_are_accepted():
    text = (
        f"* Definitive match: {UUID_A}\n"
        "- Seizure amount: 100,00\n"
        "• Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["company_uuid"] == UUID_A
    assert res["fields"]["seizure_amount"] == "100.00"
    assert res["fields"]["seized_iban"] == "DE44500105175407324931"


def test_multiline_continuation_extends_value():
    text = (
        "Definitive match: u\n"
        "Debtor address: Hauptstraße 1\n"
        "10115 Berlin\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["debtor_address"] == "Hauptstraße 1 10115 Berlin"


def test_unrecognized_key_resets_continuation_anchor():
    text = (
        "Debtor address: Hauptstraße 1\n"
        "Original file name: scan.pdf\n"
        "10115 Berlin\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    # "10115 Berlin" must NOT be appended to debtor_address after the
    # unrecognized "Original file name:" line resets the anchor.
    assert res["fields"]["debtor_address"] == "Hauptstraße 1"


def test_trailing_noise_suffix_stripped():
    text = (
        "Definitive match: u\n"
        "Debtor name: Muster GmbH Additional information\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["fields"]["debtor_name"] == "Muster GmbH"


def test_invalid_bic_is_warning_not_halt():
    text = (
        "Definitive match: u\n"
        "Creditor bic: nope\n"
        "Seized IBANs: DE44500105175407324931\n"
    )
    res = parse_jira(text)
    assert res["halted"] is False
    assert any("creditor_bic" in w for w in res["warnings"])
    assert res["fields"]["creditor_bic"] == "nope"


def test_trailing_tpd_template_block_is_ignored():
    # Porters tickets append a pre-rendered TPD template (seen on FPOPCL-27711).
    # Its colon-less first line must not be glued onto the trailing empty
    # "seized IBANs:" as a continuation (which used to falsely halt), and none
    # of the block's own "key: value" lines may leak into the parsed fields.
    text = (
        f"Definitive match: {UUID_A}\n"
        "case references: 15/296/45502 - 17/1111 - 2572/26 F\n"
        "seized IBANs:\n"
        "\n"
        "Third Party Declaration (Drittschuldnererklärung)\n"
        "Betreff: Drittschuldnererklärung gemäß § 840 ZPO\n"
        "Gläubiger: Finanzamt Flensburg\n"
        "Pfändungsbetrag: 21,688.69 EUR\n"
        "Sehr geehrte Damen und Herren,\n"
    )
    res = parse_jira(text)
    assert res["halted"] is False
    assert res["fields"]["seized_iban"] == ""
    assert res["fields"]["seizure_amount"] == ""
    assert res["fields"]["case_references"] == "15/296/45502 - 17/1111 - 2572/26 F"
    assert any("seized_iban not provided" in w for w in res["warnings"])


def test_empty_seized_ibans_line_counts_as_absent():
    text = (
        "Definitive match: u\n"
        "Seized IBANs:\n"
        "Seizure amount: 100,00\n"
    )
    res = parse_jira(text)
    assert res["fields"]["seized_iban"] == ""
    assert res["halted"] is False


def test_empty_input():
    res = parse_jira("")
    assert res["fields"] == {**empty_parsed()}
    assert res["halted"] is False


def test_returns_full_field_set():
    res = parse_jira(SAMPLE_TICKET)
    assert set(res["fields"]) == set(PARSED_FIELDS)


# --- norm_reg --------------------------------------------------------------

def test_norm_reg_strips_whitespace_and_uppercases():
    assert norm_reg("HRB 990011") == "HRB990011"
    assert norm_reg("  hrb 99 00 11 ") == "HRB990011"
    assert norm_reg("hrb990011") == "HRB990011"


def test_norm_reg_none_and_empty():
    assert norm_reg(None) == ""
    assert norm_reg("") == ""
    assert norm_reg("   ") == ""


def test_plural_match_labels_in_description():
    # Porters also write the PLURAL "Definitive matches:" (FPOPCL-31102).
    from app.parser import parse_jira

    out = parse_jira(
        "We received a seizure for X.\n"
        "* case references: 1/2/3\n"
        "* seizure amount: 10.00\n"
        "* date received: 2026-08-07\n"
        "* debtor name: X GmbH\n"
        "* definitive matches: 27e657bd-f807-4654-9c93-92687d8b0fbb\n")
    assert out["fields"]["company_uuid"] == "27e657bd-f807-4654-9c93-92687d8b0fbb"
