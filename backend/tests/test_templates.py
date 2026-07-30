"""Tests for app.templates — selection, deterministic fill, T1–T11."""
from app.templates import (
    LETTER_TEMPLATES,
    SCENARIO_TEMPLATE,
    TEMPLATE_ANCHORS,
    TEMPLATES,
    build_context,
    build_subject,
    deterministic_fill,
    select_template,
    template_kind,
)

_FIELDS = {
    "case_references": "12 M 3456/26",
    "creditor_name": "Finanzamt Berlin",
    "creditor_address": "Musterstr. 1, 10115 Berlin",
    "debtor_name": "Max Mustermann GmbH",
    "date_received": "2026-02-03",
    "seizure_amount": "1234.50",
}


def test_select_template_by_scenario():
    assert select_template("S1") == "T1"
    assert select_template("S2") == "T2"
    assert select_template("S3") == "T6"
    assert select_template("S4_NO_IBAN") == "T7"
    assert select_template("S4_IBAN") == "T8"
    assert select_template("S5") == "T9"
    assert select_template("S6A") == "T10"
    assert select_template("S6B") == "T11"
    assert select_template("INSOLVENCY") == "T4"
    assert select_template("RFI") == "T5"
    assert select_template("ROUTED_OUT") == ""


def test_templates_keys():
    assert set(TEMPLATES) == {"T1", "T2", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11"}
    assert set(SCENARIO_TEMPLATE.values()) == set(TEMPLATES) | {""}
    assert LETTER_TEMPLATES == {"T1", "T2", "T6"}


def test_t1_no_leftover_brackets():
    text = deterministic_fill("T1", _FIELDS, comments_de=[])
    assert "[" not in text
    assert "]" not in text


def test_t1_seized_amount_matches_formatted_seizure_amount():
    text = deterministic_fill("T1", _FIELDS, comments_de=[])
    # de_amount("1234.50") -> "1.234,50"
    assert "in Höhe von 1.234,50 EUR" in text
    assert "Pfändungsbetrag: 1.234,50 EUR" in text


def test_t1_has_bestehende_pfaendungen_nein():
    text = deterministic_fill("T1", _FIELDS, comments_de=[])
    assert "Bestehende Pfändungen: Nein" in text
    assert "Bestehende Pfändungen: Ja" not in text


def test_t1_has_kundenbeziehung_ja():
    text = deterministic_fill("T1", _FIELDS, comments_de=[])
    assert "Kundenbeziehung besteht: Ja" in text


def test_t1_blank_line_between_every_paragraph():
    text = deterministic_fill("T1", _FIELDS, comments_de=[])
    lines = text.split("\n")
    # Strict alternation: every non-empty line is followed by exactly one
    # blank line (except the last) — the reference document's spacing.
    for i, line in enumerate(lines):
        expect_blank = i % 2 == 1
        assert (line == "") == expect_blank, f"line {i}: {line!r}"
    assert lines[-1] == "Finom Payments B.V."
    # The four heading lines sit on their own lines (bolded by the UI on copy).
    for label in ("Gläubiger:", "Schuldner:", "Zustellungsdatum bei uns:", "Pfändungsbetrag:"):
        assert any(l.startswith(label) for l in lines), label


def test_t1_creditor_combined_and_anchors():
    text = deterministic_fill("T1", _FIELDS, comments_de=[])
    assert "Gläubiger: Finanzamt Berlin, Musterstr. 1, 10115 Berlin" in text
    assert "Schuldner: Max Mustermann GmbH" in text
    assert "Zustellungsdatum bei uns: 03.02.2026" in text
    # The title line moved to the mail subject; the body must not repeat it.
    assert "zum Aktenzeichen" not in text
    assert "§ 840" in text
    assert "Finom Payments B.V." in text


def test_build_subject_letters():
    assert build_subject("S1", _FIELDS) == (
        "Drittschuldnererklärung gemäß § 840 ZPO zum Aktenzeichen 12 M 3456/26"
    )
    assert build_subject("S3", _FIELDS).startswith("Drittschuldnererklärung")
    # Missing case ref -> subject still well-formed, just without the number.
    assert build_subject("S1", {}) == "Drittschuldnererklärung gemäß § 840 ZPO zum Aktenzeichen"


def test_build_subject_emails():
    s = build_subject("S4_NO_IBAN", _FIELDS)
    assert s.startswith("Rückfrage IBAN – ")
    assert "12 M 3456/26" in s and "Max Mustermann GmbH" in s
    assert build_subject("INSOLVENCY", _FIELDS).startswith("Insolvenz – ")
    assert build_subject("S6B", _FIELDS).startswith("Konto geschlossen – ")
    assert build_subject("ROUTED_OUT", _FIELDS) == ""


def test_t2_no_leftover_brackets():
    text = deterministic_fill("T2", _FIELDS, comments_de=["Konto wird bearbeitet."])
    assert "[" not in text
    assert "]" not in text


def test_t2_bestehende_pfaendungen_ja_and_seized_amount():
    # Same seized-amount rule as T1: seized_eur when given, else the claim.
    text = deterministic_fill("T2", _FIELDS, comments_de=["Konto wird bearbeitet."],
                              seized_eur=250.5)
    assert "Bestehende Pfändungen: Ja" in text
    assert "Bestehende Pfändungen: Nein" not in text
    assert "in Höhe von 250,50 EUR" in text
    # Total seizure amount still reflects the ticket value.
    assert "Pfändungsbetrag: 1.234,50 EUR" in text


def test_t2_comment_block_inserted():
    text = deterministic_fill("T2", _FIELDS, comments_de=["Erste Pfändung läuft.", "Zweite folgt."])
    assert "Erste Pfändung läuft." in text
    assert "Zweite folgt." in text
    # Comments appended after the Bestehende-Pfändungen line.
    idx_line = text.index("Bestehende Pfändungen: Ja")
    idx_comment = text.index("Erste Pfändung läuft.")
    assert idx_comment > idx_line


def test_t2_one_bullet_line_per_ongoing_seizure():
    text = deterministic_fill("T2", _FIELDS, comments_de=["Erste Pfändung läuft.", "Zweite folgt."])
    # Each ongoing seizure renders as its own tab-bullet ("\t• ") line, the
    # bullets sit adjacent in one block, and the block is its own paragraph.
    assert "\t• Erste Pfändung läuft.\n\t• Zweite folgt." in text
    assert "Bestehende Pfändungen: Ja\n\n\t• Erste Pfändung läuft." in text
    assert "\t• Zweite folgt.\n\nVerpflichtungen" in text


def test_t2_empty_and_blank_comments_are_skipped():
    text = deterministic_fill("T2", _FIELDS, comments_de=["", "  ", "Nur diese."])
    assert text.count("\t• ") == 1
    assert "\t• Nur diese." in text


def test_t2_no_comments_leaves_no_stray_blank_paragraph():
    text = deterministic_fill("T2", _FIELDS, comments_de=[])
    assert "\t•" not in text
    assert "\n\n\n" not in text
    assert "Bestehende Pfändungen: Ja\n\nVerpflichtungen" in text


def test_t2_kundenbeziehung_ja():
    text = deterministic_fill("T2", _FIELDS, comments_de=[])
    assert "Kundenbeziehung besteht: Ja" in text


def test_build_context_seized_amount_same_for_both_templates():
    ctx1 = build_context("T1", _FIELDS, comments_de=[])
    ctx2 = build_context("T2", _FIELDS, comments_de=[])
    # No seized_eur -> both fall back to the claim.
    assert ctx1["[Seized amount]"] == "1.234,50"
    assert ctx2["[Seized amount]"] == "1.234,50"


def test_build_context_combined_creditor_key():
    ctx = build_context("T1", _FIELDS, comments_de=[])
    assert ctx["[Creditor], [creditor Address]"] == "Finanzamt Berlin, Musterstr. 1, 10115 Berlin"
    assert ctx["[Creditor]"] == "Finanzamt Berlin"
    assert ctx["[creditor Address]"] == "Musterstr. 1, 10115 Berlin"


def test_deterministic_fill_handles_missing_fields():
    text = deterministic_fill("T1", {}, comments_de=[])
    assert "[" not in text
    # de_amount(None) -> "0,00"; de_date("") -> ""
    assert "Pfändungsbetrag: 0,00 EUR" in text


def test_build_context_uses_seized_eur_for_t1():
    ctx = build_context("T1", _FIELDS, comments_de=[], seized_eur=500.0)
    assert ctx["[Seized amount]"] == "500,00"
    # The Pfändungsbetrag (claim) is unaffected by the seizable amount.
    assert ctx["[Total seizure amount]"] == "1.234,50"


def test_build_context_t2_honors_seized_eur():
    ctx = build_context("T2", _FIELDS, comments_de=[], seized_eur=500.0)
    assert ctx["[Seized amount]"] == "500,00"


def test_build_context_seized_eur_none_falls_back_to_claim():
    ctx = build_context("T1", _FIELDS, comments_de=[], seized_eur=None)
    assert ctx["[Seized amount]"] == "1.234,50"


def test_deterministic_fill_t1_with_seized_eur():
    text = deterministic_fill("T1", _FIELDS, comments_de=[], seized_eur=500.0)
    assert "in Höhe von 500,00 EUR" in text
    assert "Pfändungsbetrag: 1.234,50 EUR" in text


# --- T6 (Scenario 3) -----------------------------------------------------------


def test_t6_flags_for_s3():
    text = deterministic_fill("T6", _FIELDS, comments_de=[], seized_eur=0.0, scenario="S3")
    assert "Kundenbeziehung besteht: Nein" in text
    assert "Bestehende Pfändungen: N/A" in text
    assert "in Höhe von 0,00 EUR" in text
    assert "[" not in text


def test_t6_defaults_to_s3_flags_without_scenario():
    text = deterministic_fill("T6", _FIELDS, comments_de=[])
    assert "Kundenbeziehung besteht: Nein" in text


# --- emails T4/T5/T7–T11 ---------------------------------------------------------


def test_all_email_templates_fill_clean():
    for tid in ("T4", "T5", "T7", "T8", "T9"):
        text = deterministic_fill(tid, _FIELDS, comments_de=[])
        assert "[" not in text and "]" not in text, tid
        for anchor in TEMPLATE_ANCHORS[tid]:
            assert anchor in text, (tid, anchor)


def test_t10_uses_case_references():
    text = deterministic_fill("T10", _FIELDS, comments_de=[])
    assert "Protokollnummer 12 M 3456/26" in text
    assert "[" not in text


def test_t11_restbetrag_uses_seized_eur():
    text = deterministic_fill("T11", _FIELDS, comments_de=[], seized_eur=250.5, scenario="S6B")
    assert "Referenznummer 12 M 3456/26" in text
    assert "Restbetrag in Höhe von 250,50 EUR" in text
    assert "[" not in text


def test_template_kind():
    assert template_kind("T1") == "letter"
    assert template_kind("T2") == "letter"
    assert template_kind("T6") == "letter"
    assert template_kind("T5") == "guidance"
    for tid in ("T4", "T7", "T8", "T9", "T10", "T11"):
        assert template_kind(tid) == "email", tid
    assert template_kind("") == ""


def test_anchors_present_in_own_templates():
    # Each template body must contain its own anchors (guards can't be stricter
    # than the deterministic output).
    for tid, anchors in TEMPLATE_ANCHORS.items():
        filled = deterministic_fill(tid, _FIELDS, comments_de=["Ein Eintrag."])
        for anchor in anchors:
            assert anchor in filled, (tid, anchor)
