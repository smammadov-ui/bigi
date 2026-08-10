"""Graded German address comparison — app.addresses."""
from __future__ import annotations

from app.addresses import compare_addresses, parse_ticket_address


def _bo(street="Hauptstraße", house="1", pc="60311", city="Frankfurt"):
    return {"street": street, "houseNo": house, "postCode": pc, "city": city}


def test_parse_ticket_address_porters_shape():
    t = parse_ticket_address("Ortenauer Str. 16, 77767, , Appenweier")
    assert t["street"] == "Ortenauer Str. 16"
    assert t["postcode"] == "77767"
    assert t["city"] == "Appenweier"
    assert t["houses"] == {16}


def test_strong_abbreviation_and_umlauts():
    r = compare_addresses("Hauptstr. 1, 60311 Frankfurt", _bo())
    assert r["grade"] == "strong"
    r = compare_addresses("Gutleutstrasse 118, 60327 Frankfurt",
                          _bo("Gutleutstraße", "118-124", "60327"))
    assert r["grade"] == "strong"        # range 118-124 contains 118


def test_strong_survives_small_typo():
    r = compare_addresses("Ortenauer Str. 16, 77767 Appenweier",
                          _bo("Ortenaür Strasse", "16", "77767", "Appenweier"))
    assert r["grade"] == "strong"


def test_mismatch_same_postcode_different_street():
    # The doppelgänger hole: same postcode, clearly different street.
    r = compare_addresses("Musterweg 5, 60311 Frankfurt",
                          _bo("Gutleutstraße", "99", "60311"))
    assert r["grade"] == "mismatch"
    assert "different street" in r["detail"]


def test_mismatch_postcode_differs():
    r = compare_addresses("Hauptstr. 1, 99999 X", _bo())
    assert r["grade"] == "mismatch"


def test_weak_street_missing():
    r = compare_addresses("60311 Frankfurt", _bo())
    assert r["grade"] == "weak"


def test_weak_house_number_conflict():
    r = compare_addresses("Hauptstr. 7, 60311 Frankfurt", _bo(house="1"))
    assert r["grade"] == "weak"
    assert "house number differs" in r["detail"]


def test_unknown_when_postcode_missing():
    assert compare_addresses("Hauptstr. 1, Frankfurt", _bo())["grade"] == "unknown"
    assert compare_addresses("Hauptstr. 1, 60311", {"street": "X"})["grade"] == "unknown"


def test_fixture_zip_key_supported():
    r = compare_addresses("Hauptstr. 1, 60311 Frankfurt",
                          {"street": "Hauptstr. 1", "zip": "60311", "city": "Frankfurt"})
    assert r["grade"] == "strong"


def test_display_strings_present():
    r = compare_addresses("Hauptstr. 1, 60311 Frankfurt", _bo())
    assert "60311" in r["ticket"] and "60311" in r["account"]


def test_floor_prefix_ignored_ii_og():
    # Live case FPOPCL-31056: BO stores "II OG, Am Hehsel 38".
    r = compare_addresses("Am Hehsel 38, , 22339, Hamburg",
                          {"street": "II OG, Am Hehsel 38", "postCode": "22339",
                           "city": "Hamburg"})
    assert r["grade"] == "strong"


def test_co_line_ignored():
    r = compare_addresses("Hauptstr. 1, 60311 Frankfurt",
                          {"street": "c/o Steuerbüro Meyer, Hauptstraße 1",
                           "postCode": "60311", "city": "Frankfurt"})
    assert r["grade"] == "strong"


def test_containment_does_not_rescue_different_street():
    r = compare_addresses("Musterweg 5, 60311 X",
                          {"street": "II OG, Gutleutstraße", "houseNo": "5",
                           "postCode": "60311"})
    assert r["grade"] == "mismatch"
