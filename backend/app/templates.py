"""T1–T11 German §840 declaration + email templates + deterministic fill.

The template bodies are VERBATIM German (placeholders in ``[...]``), covering
all 11 scenarios (spec/templates_de.md of the full app). Layout follows mini's
mature conventions:

* the title/subject line lives in the MAIL SUBJECT (``build_subject``), not the
  body;
* a blank line between every heading line / paragraph (mirrors the reference
  document and survives plain-text + rich-clipboard paste);
* one ``\\t• `` bullet line per ongoing seizure in the ``[Comment]`` slot,
  built upstream from BO's structured fields.

Scenario -> template:  S1→T1, S2→T2, S3→T6, S4_NO_IBAN→T7, S4_IBAN→T8, S5→T9,
S6A→T10, S6B→T11, INSOLVENCY→T4, RFI→T5, ROUTED_OUT→(none).
T6 reuses T1's body — only the flag values differ (Kundenbeziehung: Nein,
Bestehende Pfändungen: N/A, 0,00).

Pure, no I/O. Used by ``llm.compose`` as the no-LLM deterministic fallback.
"""
from __future__ import annotations

import re

from .formatting import de_amount, de_date
from .schemas import Scenario

# ---------------------------------------------------------------------------
# Letter subject (T1/T2/T6) — kept out of the body (mini convention).
# ---------------------------------------------------------------------------
_LETTER_SUBJECT = "Drittschuldnererklärung gemäß § 840 ZPO zum Aktenzeichen [Case number]"

# Email subjects: "<prefix> – Pfändungssache <case> – <debtor>".
_EMAIL_SUBJECT_PREFIX: dict[str, str] = {
    "T4": "Insolvenz",
    "T5": "Auskunftsersuchen",
    "T7": "Rückfrage IBAN",
    "T8": "Rückfrage IBAN",
    "T9": "Pfändung gegen Privatperson",
    "T10": "Konto in Schließung",
    "T11": "Konto geschlossen",
}

# ---------------------------------------------------------------------------
# T1 — Scenario 1: normal TPD (blank line between every paragraph).
# ---------------------------------------------------------------------------
_T1 = """Gläubiger: [Creditor], [creditor Address]

Schuldner: [debtor name]

Zustellungsdatum bei uns: [Delivered on]

Pfändungsbetrag: [Total seizure amount] EUR

Sehr geehrte Damen und Herren,

unter Bezugnahme auf den uns zugestellten Pfändungs- und Überweisungsbeschluss geben wir hiermit fristgerecht die folgende Erklärung als Drittschuldner gemäß § 840 der Zivilprozessordnung (ZPO) ab:

Der Schuldner unterhält eine Geschäftsbeziehung zu uns. Eine Forderung des Schuldners gegen uns besteht grundsätzlich. [Kundenbeziehung besteht: Ja].

Die gepfändete Forderung besteht derzeit in Höhe von [Seized amount] EUR.

Die gepfändete Forderung betrifft Guthaben auf Konten, die keine Pfändungsschutzkonten im Sinne des § 850k ZPO sind. Derzeit ist kein pfändbares Guthaben verfügbar, das den geltenden Freibetrag übersteigt.

Andere Personen machen derzeit keine Ansprüche auf die gepfändeten Forderungen geltend. Die Pfändung künftiger Forderungen ist vorgemerkt. Eigene vorrangige Ansprüche unsererseits bestehen nicht. [Bestehende Pfändungen: Nein].

Verpflichtungen aus der Nutzung von Debitkarten durch den Schuldner können anfallen, deren genaue Höhe erst zu einem späteren Zeitpunkt festgestellt werden kann. Insofern behalten wir uns unsere Pfand- und Aufrechnungsrechte vor.

Wir werden eine Überweisung des pfändbaren Guthabens veranlassen, sobald und soweit dies rechtlich zulässig ist. Falls die Pfändungsforderung derzeit nicht oder nicht vollständig befriedigt werden kann, werden wir auf die Angelegenheit zurückkommen, sobald ein entsprechendes Guthaben verfügbar ist.

Mit freundlichen Grüßen,

Finom Payments B.V."""

# T2 = T1 with the Bestehende-Pfändungen line changed and the comment block
# appended as its own paragraph (one "\t• " bullet line per ongoing seizure).
_T2 = _T1.replace(
    "Eigene vorrangige Ansprüche unsererseits bestehen nicht. [Bestehende Pfändungen: Nein].",
    "Eigene vorrangige Ansprüche unsererseits bestehen nicht. [Bestehende Pfändungen: Ja]\n\n[Comment]",
)

# T6 — Scenario 3 (closed / onboarding): T1's body; the flag values are
# ctx-driven (Kundenbeziehung: Nein, 0,00, Bestehende Pfändungen: N/A).
_T6 = _T1

# ---------------------------------------------------------------------------
# Emails (T4/T5/T7–T11) — verbatim wording, blank-line paragraph layout.
# ---------------------------------------------------------------------------
_T4 = """Sehr geehrte Damen und Herren,

bitte beachten Sie, dass wir diese Pfändung aufgrund eines laufenden Insolvenzverfahrens über das Konto derzeit nicht bearbeiten können. Wir haben die Forderung vorgemerkt und werden die Bearbeitung Ihres Ersuchens priorisieren, sobald die Angelegenheit geklärt ist.

Vielen Dank für Ihr Verständnis."""

# T5 is operator guidance, not a customer letter: an MNL22 is an information
# request — gather the data, create nothing, send no §840 declaration.
_T5 = """Sehr geehrte Damen und Herren,

dies ist keine Pfändung, sondern ein Auskunftsersuchen. Die ersuchende Behörde benötigt Daten (z. B. IP-Protokolle, Kontostand, Kontoauszüge). Bitte beschaffen Sie die angeforderten Informationen aus dem Back Office und stellen Sie sie der Behörde bereit. Es wird keine Pfändung angelegt und keine Drittschuldnererklärung gemäß § 840 ZPO versandt."""

_T7 = """Sehr geehrte Damen und Herren,

wir hoffen, dass es Ihnen gut geht.

Leider konnten wir die betreffende Person anhand der uns vorliegenden Informationen nicht in unserem System finden. Um den entsprechenden Datensatz eindeutig zu identifizieren und Ihr Anliegen weiterbearbeiten zu können, bitten wir Sie höflich, uns die zugehörige IBAN mitzuteilen.

Sobald wir diese Information erhalten haben, werden wir den Vorgang umgehend weiterverfolgen.

Vielen Dank für Ihre Unterstützung. Wir freuen uns auf Ihre Rückmeldung.

Mit freundlichen Grüßen"""

_T8 = """Sehr geehrte Damen und Herren,

wir hoffen, dass es Ihnen gut geht.

Leider konnten wir die betreffende Person anhand der uns vorliegenden Informationen nicht in unserem System identifizieren. Zudem haben wir festgestellt, dass die von Ihnen angegebene IBAN in unserem System nicht erfasst ist bzw. nicht zugeordnet werden kann.

Um den entsprechenden Datensatz eindeutig zu identifizieren und Ihr Anliegen weiterbearbeiten zu können, bitten wir Sie daher höflich, uns die korrekte IBAN mitzuteilen.

Sobald wir diese Information erhalten haben, werden wir den Vorgang umgehend weiterverfolgen.

Vielen Dank für Ihre Unterstützung. Wir freuen uns auf Ihre Rückmeldung.

Mit freundlichen Grüßen"""

_T9 = """Sehr geehrte Damen und Herren,

wir hoffen, es geht Ihnen gut.

Die vorliegende Pfändungsverfügung richtet sich gegen eine Privatperson und nicht gegen ein Unternehmen. Da es sich bei dem betroffenen Konto um ein Geschäftskonto handelt, können wir die Pfändung nicht bearbeiten.

Zu Ihrer Orientierung haben wir das von Ihnen erhaltene Dokument beigefügt, um eindeutig zu kennzeichnen, auf welchen Vorgang wir uns beziehen.

Mit freundlichen Grüßen"""

_T10 = """Sehr geehrte Damen und Herren,

wir hoffen, dass es Ihnen gut geht.

Leider können wir die eingegangene Pfändung mit der Protokollnummer [case references] nicht weiter bearbeiten, da sich das betreffende Konto bereits vor dem Datum des Eingangs Ihres Pfändungsersuchens im Prozess der Kontoschließung befand.

Zu Ihrer Information fügen wir das von Ihnen erhaltene Dokument bei, um eindeutig zu belegen, auf welchen Vorgang wir uns beziehen.

Für weitere Fragen stehen wir Ihnen selbstverständlich gerne zur Verfügung.

Mit freundlichen Grüßen"""

_T11 = """Sehr geehrte Damen und Herren,

wir hoffen, es geht Ihnen gut.

Leider können wir Ihren Pfändungsantrag mit der Referenznummer [Case number] nicht bearbeiten, da das betreffende Konto bereits vor Eingang Ihres Antrags geschlossen wurde. Wir können Ihnen daher nur den Restbetrag in Höhe von [Seized amount] EUR überweisen.

Zur Information fügen wir das von Ihnen bereitgestellte Dokument bei, um den betreffenden Fall eindeutig zu verdeutlichen.

Bei weiteren Fragen stehen wir Ihnen gerne zur Verfügung.

Mit freundlichen Grüßen"""

TEMPLATES: dict[str, str] = {
    "T1": _T1, "T2": _T2, "T4": _T4, "T5": _T5, "T6": _T6,
    "T7": _T7, "T8": _T8, "T9": _T9, "T10": _T10, "T11": _T11,
}

# scenario -> template id (ROUTED_OUT has no customer document).
SCENARIO_TEMPLATE: dict[str, str] = {
    Scenario.S1.value: "T1",
    Scenario.S2.value: "T2",
    Scenario.S3.value: "T6",
    Scenario.S4_NO_IBAN.value: "T7",
    Scenario.S4_IBAN.value: "T8",
    Scenario.S5.value: "T9",
    Scenario.S6A.value: "T10",
    Scenario.S6B.value: "T11",
    Scenario.INSOLVENCY.value: "T4",
    Scenario.RFI.value: "T5",
    Scenario.ROUTED_OUT.value: "",
}

# Templates rendered as a §840 letter vs. an outgoing email; T5 is internal
# operator guidance (data gathering).
LETTER_TEMPLATES = frozenset({"T1", "T2", "T6"})

# Anchors that must survive in any (LLM-)composed output for it to be accepted.
TEMPLATE_ANCHORS: dict[str, tuple[str, ...]] = {
    "T1": ("§ 840", "Finom Payments B.V."),
    "T2": ("§ 840", "Finom Payments B.V."),
    "T6": ("§ 840", "Finom Payments B.V."),
    "T4": ("Insolvenzverfahrens",),
    "T5": ("Auskunftsersuchen",),
    "T7": ("IBAN",),
    "T8": ("IBAN",),
    "T9": ("Privatperson",),
    "T10": ("Kontoschließung",),
    "T11": ("Restbetrag",),
}


def select_template(scenario: str) -> str:
    """Template id for a scenario ("" for ROUTED_OUT)."""
    return SCENARIO_TEMPLATE.get(scenario, "")


def template_kind(template_id: str) -> str:
    """"letter" (T1/T2/T6), "guidance" (T5), or "email" (the rest); "" for none."""
    if not template_id:
        return ""
    if template_id in LETTER_TEMPLATES:
        return "letter"
    if template_id == "T5":
        return "guidance"
    return "email"


def build_subject(scenario: str, fields: dict) -> str:
    """Mail subject for the document (the body does not repeat it)."""
    template_id = select_template(scenario)
    fields = fields or {}
    case_ref = str(fields.get("case_references") or "")
    debtor = str(fields.get("debtor_name") or "")
    if not template_id:
        return ""
    if template_id in LETTER_TEMPLATES:
        return _LETTER_SUBJECT.replace("[Case number]", case_ref).strip()
    prefix = _EMAIL_SUBJECT_PREFIX.get(template_id, "")
    base = f"Pfändungssache {case_ref} – {debtor}".strip(" –")
    return f"{prefix} – {base}" if prefix else base


# When no scenario is given (standalone template use), each letter template
# implies its canonical scenario's flags.
_DEFAULT_SCENARIO_FOR_TEMPLATE = {"T1": "S1", "T2": "S2", "T6": "S3"}


def _flags_for(scenario: str) -> tuple[str, str]:
    """(Kundenbeziehung, Bestehende Pfändungen) flag values per scenario."""
    kunde = {"S1": "Ja", "S2": "Ja", "S3": "Nein"}.get(scenario, "N/A")
    bestehende = {"S1": "Nein", "S2": "Ja", "S3": "N/A"}.get(scenario, "N/A")
    return kunde, bestehende


def build_context(template_id: str, fields: dict, comments_de: list[str],
                  seized_eur=None, scenario: str = "") -> dict:
    """Return the placeholder -> value mapping for ``template_id``.

    ``[Seized amount]`` renders ``seized_eur`` (computed upstream by
    :mod:`app.amounts`: the own-case seizure's BO ``seizedAmount``, falling back
    to ``min(claim, available balance)``; for T6/S3 it is 0,00). When unknown
    (``None``) it falls back to the claimed amount. The flag placeholders are
    scenario-driven so T6 can reuse T1's body.
    """
    fields = fields or {}
    # One bullet = one line: inner newlines would break the letter layout and
    # the one-bullet-per-seizure output guard.
    comments_de = [re.sub(r"\s+", " ", str(c)).strip() for c in (comments_de or [])]
    comments_de = [c for c in comments_de if c]

    creditor_name = str(fields.get("creditor_name") or "")
    creditor_address = str(fields.get("creditor_address") or "")
    amount_de = de_amount(fields.get("seizure_amount"))
    if seized_eur is not None:
        seized_amount = de_amount(seized_eur)
    else:
        seized_amount = amount_de
    kunde, bestehende = _flags_for(
        scenario or _DEFAULT_SCENARIO_FOR_TEMPLATE.get(template_id, ""))

    return {
        "[Case number]": str(fields.get("case_references") or ""),
        "[case references]": str(fields.get("case_references") or ""),
        # Combined first so it resolves before the individual substrings.
        "[Creditor], [creditor Address]": f"{creditor_name}, {creditor_address}",
        "[Creditor]": creditor_name,
        "[creditor Address]": creditor_address,
        "[debtor name]": str(fields.get("debtor_name") or ""),
        "[Delivered on]": de_date(fields.get("date_received")),
        "[Total seizure amount]": amount_de,
        "[Seized amount]": seized_amount,
        # One bullet line per ongoing seizure — a seizure must never be dropped
        # or merged with another (the declaration lists each one). "\t• " is a
        # real bullet glyph (U+2022) so the letter reads as a proper bullet list
        # in plain text, the preview, and the rich-HTML clipboard flavor.
        "[Comment]": "\n".join(f"\t• {c}" for c in comments_de),
        # Flag placeholders: scenario-resolved, brackets stripped.
        "[Kundenbeziehung besteht: Ja]": f"Kundenbeziehung besteht: {kunde}",
        "[Bestehende Pfändungen: Nein]": f"Bestehende Pfändungen: {bestehende}",
        # Only present in T2; mapped so no bracket placeholder survives the fill.
        "[Bestehende Pfändungen: Ja]": "Bestehende Pfändungen: Ja",
    }


def deterministic_fill(template_id: str, fields: dict, comments_de: list[str],
                       seized_eur=None, scenario: str = "") -> str:
    """Replace every ``[...]`` placeholder in ``TEMPLATES[template_id]``.

    Longer keys are replaced first so ``[Total seizure amount] EUR`` and
    ``[Creditor], [creditor Address]`` resolve before their substrings. No bracket
    placeholder may remain. Used as the no-LLM fallback (comments inserted untranslated).
    """
    text = TEMPLATES[template_id]
    context = build_context(template_id, fields, comments_de,
                            seized_eur=seized_eur, scenario=scenario)
    for key in sorted(context, key=len, reverse=True):
        text = text.replace(key, context[key])
    # T2 with no comments leaves an empty [Comment] paragraph behind.
    return re.sub(r"\n{3,}", "\n\n", text).strip()
