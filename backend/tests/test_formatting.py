"""Tests for app.formatting — German-locale number/date formatting."""
from app.formatting import de_amount, de_date, parse_date_iso, parse_decimal


# --- parse_decimal: US + German + currency text + edge cases ---------------

def test_parse_decimal_us_thousands():
    assert parse_decimal("14,250.00") == 14250.0


def test_parse_decimal_german_thousands():
    assert parse_decimal("14.250,00") == 14250.0


def test_parse_decimal_strips_currency_text():
    assert parse_decimal("14,250.00 EUR") == 14250.0
    assert parse_decimal("€ 6.771,29") == 6771.29
    assert parse_decimal("1.234,56 USD") == 1234.56


def test_parse_decimal_plain_numbers():
    assert parse_decimal("1250") == 1250.0
    assert parse_decimal("1250.5") == 1250.5
    assert parse_decimal(1250) == 1250.0
    assert parse_decimal(1250.5) == 1250.5


def test_parse_decimal_grouping_only_dot():
    # 14.250 with no decimal part -> grouping dot, not 14.25
    assert parse_decimal("14.250") == 14250.0


def test_parse_decimal_grouping_only_comma():
    # 14,250 with three trailing digits -> grouping comma
    assert parse_decimal("14,250") == 14250.0


def test_parse_decimal_comma_decimal():
    assert parse_decimal("12,5") == 12.5
    assert parse_decimal("12,50") == 12.5


def test_parse_decimal_none_and_garbage():
    assert parse_decimal(None) is None
    assert parse_decimal("") is None
    assert parse_decimal("abc") is None
    assert parse_decimal("-") is None
    assert parse_decimal(".") is None
    assert parse_decimal(",") is None


def test_parse_decimal_negative():
    assert parse_decimal("-1.234,56") == -1234.56


# --- de_amount -------------------------------------------------------------

def test_de_amount_us_input_to_german():
    assert de_amount("14,250.00 EUR") == "14.250,00"


def test_de_amount_zero():
    assert de_amount(0) == "0,00"


def test_de_amount_german_roundtrip():
    assert de_amount("6.771,29") == "6.771,29"


def test_de_amount_plain_float():
    assert de_amount(1234.5) == "1.234,50"


def test_de_amount_unparseable_uses_default():
    assert de_amount("abc") == "0,00"
    assert de_amount(None) == "0,00"
    assert de_amount("xxx", default="-") == "-"


def test_de_amount_millions_grouping():
    assert de_amount("1234567.89") == "1.234.567,89"


# --- parse_date_iso --------------------------------------------------------

def test_parse_date_iso_already_iso():
    assert parse_date_iso("2026-02-03") == "2026-02-03"


def test_parse_date_iso_german():
    assert parse_date_iso("03.02.2026") == "2026-02-03"


def test_parse_date_iso_slashes():
    assert parse_date_iso("2026/02/03") == "2026-02-03"
    assert parse_date_iso("03/02/2026") == "2026-02-03"


def test_parse_date_iso_pads_single_digits():
    assert parse_date_iso("3.2.2026") == "2026-02-03"


def test_parse_date_iso_empty_and_unknown():
    assert parse_date_iso("") == ""
    assert parse_date_iso(None) == ""
    assert parse_date_iso("not a date") == "not a date"


# --- de_date ---------------------------------------------------------------

def test_de_date_from_iso():
    assert de_date("2026-02-03") == "03.02.2026"


def test_de_date_from_german():
    assert de_date("03.02.2026") == "03.02.2026"


def test_de_date_empty_and_default():
    assert de_date("") == ""
    assert de_date(None) == ""
    assert de_date("", default="-") == "-"


def test_de_date_epoch_seconds():
    # 2021-01-01T00:00:00Z
    assert de_date(1609459200) == "01.01.2021"


def test_iso_date_any_epoch_and_strings():
    from app.formatting import iso_date_any

    assert iso_date_any(1769904000000) == "2026-02-01"   # epoch ms (real BO)
    assert iso_date_any(1769904000) == "2026-02-01"      # epoch seconds
    assert iso_date_any("1769904000000") == "2026-02-01"
    assert iso_date_any("2026-03-01T00:00:00Z") == "2026-03-01"
    assert iso_date_any("05.03.2026") == "2026-03-05"
    assert iso_date_any("") == "" and iso_date_any(None) == ""
    assert iso_date_any("garbage") == ""


def test_de_date_epoch_milliseconds():
    assert de_date(1769904000000) == "01.02.2026"
    assert de_date(1769904000) == "01.02.2026"
