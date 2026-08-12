"""Manual mode — the operator-editable DECISION SET (docs/manual-mode-plan.md).

Auto proposes, operator disposes: every pipeline run distills its choices into
a decision set (template, per-seizure roles, amounts, IBAN, email slots). The
UI edits it and the document is recomposed as a PURE function of it — no BO
call, no new pipeline run. Contradictions produce non-blocking warnings; the
operator is the authority. bigi still never writes to Back-Office.

Shapes
------
``manual`` block on every pipeline result::

    {
      "decisions": { ...the auto-filled, operator-editable decision set... },
      "auto":      { "scenario", "template" },          # what auto concluded
      "options":   { "templates": TEMPLATE_CATALOG,
                     "wallets": [{"name","iban","currency"}],
                     "status_bucket", "account_type" },
      "context":   { "fields": {...parsed ticket fields...} },
    }

``POST /api/declaration/compose`` body = ``{decisions, context, auto}`` →
``{declaration, warnings, manual_template}``.
"""
from __future__ import annotations

from . import llm
from .formatting import de_amount
from .schemas import BigiError
from .settings_store import llm_config
from .templates import (
    TEMPLATES,
    build_subject_for_template,
    template_kind,
)

# Operator-facing template catalog: label + what each template needs. The
# ``needs`` strings render as the Decision panel's checklist.
TEMPLATE_CATALOG: list[dict] = [
    {"id": "T1", "kind": "letter", "family": "§840 letters",
     "label": "T1 — declaration, no other seizures (S1)",
     "needs": ["own-case / seizable amount ([Seized amount])"]},
    {"id": "T2", "kind": "letter", "family": "§840 letters",
     "label": "T2 — declaration listing prior seizures (S2)",
     "needs": ["at least one seizure set to 'report'",
               "own-case / seizable amount ([Seized amount])"]},
    {"id": "T6", "kind": "letter", "family": "§840 letters",
     "label": "T6 — declaration, no client relationship (S3)",
     "needs": []},
    {"id": "T4", "kind": "email", "family": "emails",
     "label": "T4 — insolvency notice (MNL21)",
     "needs": ["recipient email"]},
    {"id": "T7", "kind": "email", "family": "emails",
     "label": "T7 — ask creditor for an IBAN (no match, none provided)",
     "needs": ["recipient email"]},
    {"id": "T8", "kind": "email", "family": "emails",
     "label": "T8 — ask creditor for the correct IBAN (no match, IBAN unknown)",
     "needs": ["recipient email"]},
    {"id": "T9", "kind": "email", "family": "emails",
     "label": "T9 — seizure targets a private person (S5)",
     "needs": ["recipient email"]},
    {"id": "T10", "kind": "email", "family": "emails",
     "label": "T10 — account already closing (S6A)",
     "needs": ["recipient email"]},
    {"id": "T11", "kind": "email", "family": "emails",
     "label": "T11 — account closed, remainder transfer (S6B)",
     "needs": ["recipient email", "remainder amount ([Seized amount])"]},
    {"id": "T5", "kind": "guidance", "family": "internal guidance",
     "label": "T5 — RFI: gather data, no declaration (MNL22)",
     "needs": []},
]

_META_FIELD_KEYS = frozenset({"warnings", "halted", "halt_reasons"})

# Letter templates whose [Seized amount] is the actually declared figure.
_AMOUNT_TEMPLATES = ("T1", "T2", "T11")


def _row(r: dict, role: str, note: str) -> dict:
    """One BO seizure row in decision-set shape (role editable, rest context)."""
    return {
        "id": r.get("id"),
        "case_ref": r.get("caseNumber") or "",
        "status": r.get("status") or "",
        "created": r.get("created") or "",
        "amount": r.get("seized_amount"),
        "claim": r.get("claim_amount"),
        "description_de": r.get("description_de") or "",
        "comment": r.get("comment") or "",
        "role": role,
        "auto_role": role,
        "note": note,
    }


def build_manual(*, parsed: dict | None, scenario=None, plan=None,
                 declaration=None, account=None, balance=None,
                 seizure_check=None, wallets=None) -> dict:
    """The ``manual`` block for a pipeline result — safe on partial data
    (pending selection / halted / routed out), so dead ends stay completable."""
    parsed = parsed or {}
    plan = plan or {}
    declaration = declaration or {}
    account = account or {}
    balance = balance or {}
    sc = seizure_check or {}

    rows: list[dict] = []
    for r in sc.get("seizures") or []:
        rows.append(_row(r, "report", "competing prior seizure"))
    for r in sc.get("ignored_same_case") or []:
        rows.append(_row(r, "own", "this ticket's own case"))
    for r in sc.get("ignored_later") or []:
        rows.append(_row(r, "ignore", "junior — created after this case"))

    own_amounts = [r["amount"] for r in rows if r["role"] == "own" and r["amount"] is not None]
    fields = {k: v for k, v in parsed.items() if k not in _META_FIELD_KEYS}

    decisions = {
        "template": plan.get("template") or "",
        "subject": declaration.get("subject") or "",
        "recipient_email": str(fields.get("creditor_email") or ""),
        "seized_iban": {
            "value": account.get("seized_iban") or "",
            "source": account.get("seized_iban_source") or "",
        },
        "seizures": rows,
        "own_case_amount": own_amounts[0] if own_amounts else None,
        "available_eur": balance.get("available_eur"),
        "seizable_eur": balance.get("seizable_eur"),
    }
    return {
        "decisions": decisions,
        "auto": {"scenario": scenario, "template": plan.get("template") or ""},
        "options": {
            "templates": TEMPLATE_CATALOG,
            "wallets": [
                {"name": w.get("name") or "", "iban": w.get("iban") or "",
                 "currency": w.get("currency") or ""}
                for w in (wallets or []) if w.get("iban")
            ],
            "status_bucket": account.get("status_bucket") or "",
            "account_type": account.get("account_type") or "",
        },
        "context": {"fields": fields},
    }


def _num(v):
    """Lenient number: None/'' -> None; '1.234,56' / '1234.56' -> float."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    if "," in s:  # German decimal comma (thousands dots)
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def validate_decisions(decisions: dict, context: dict, auto: dict | None) -> list[str]:
    """Non-blocking contradiction warnings (+ the passive cross-hint). The
    operator is the authority — nothing here stops composing."""
    warnings: list[str] = []
    t = decisions.get("template") or ""
    rows = decisions.get("seizures") or []
    reported = [r for r in rows if r.get("role") == "report"]
    own = [r for r in rows if r.get("role") == "own"]
    bucket = str((context.get("options") or {}).get("status_bucket")
                 or context.get("status_bucket") or "")
    kind = template_kind(t)

    if t == "T1" and reported:
        warnings.append(
            f"T1 declares no other seizures, but {len(reported)} row(s) are set "
            "to 'report' — T2 fits that selection")
    if t == "T2" and not reported:
        warnings.append(
            "T2 lists prior seizures but no row is set to 'report' — T1 fits "
            "that selection")
    if t in ("T1", "T2") and rows and not own:
        warnings.append(
            "no row is marked as this ticket's own case — verify the seizure "
            "was submitted in BO")
    if t in _AMOUNT_TEMPLATES and _num(decisions.get("seizable_eur")) is None:
        warnings.append(
            "seizable amount is empty — [Seized amount] falls back to the "
            "ticket's claim; review before sending")
    if kind == "email" and not str(decisions.get("recipient_email") or "").strip():
        warnings.append("email template without a recipient address")
    if bucket == "OPEN" and t in ("T6", "T10", "T11"):
        warnings.append(f"{t} is a closed/closing-account document but the account is OPEN")
    if bucket == "CLOSED" and t in ("T1", "T2"):
        warnings.append(f"{t} declares an active client relationship but the account is CLOSED")
    avail, seiz = _num(decisions.get("available_eur")), _num(decisions.get("seizable_eur"))
    if avail is not None and seiz is not None and seiz > avail:
        warnings.append(
            f"seizable ({de_amount(seiz)} EUR) exceeds the available balance "
            f"({de_amount(avail)} EUR)")
    return warnings


def compose_from_decisions(db, decisions: dict, context: dict,
                           auto: dict | None = None) -> dict:
    """PURE recompose: decision set + echoed context -> document + warnings.

    No BO calls, no re-identification — exactly what the auto pipeline's
    compose step does, parameterized by the operator's decisions.
    """
    decisions = decisions or {}
    context = context or {}
    auto = auto or {}
    template_id = str(decisions.get("template") or "").strip()
    if not template_id:
        raise BigiError("decisions.template is required")
    if template_id not in TEMPLATES:
        raise BigiError(f"unknown template {template_id!r} (valid: {', '.join(sorted(TEMPLATES))})")

    fields = dict(context.get("fields") or {})
    # One German bullet per REPORTED seizure — same construction as the auto
    # pipeline (structured description first, raw comment as fallback).
    comments = [
        (r.get("description_de") or r.get("comment") or "").strip()
        for r in decisions.get("seizures") or []
        if r.get("role") == "report"
    ]
    comments = [c for c in comments if c]

    seized_eur = _num(decisions.get("seizable_eur"))
    # Flags (Kundenbeziehung / Bestehende Pfändungen): the auto scenario only
    # applies while the operator keeps auto's template; an overridden letter
    # uses its canonical scenario (T1→S1, T2→S2, T6→S3).
    scenario_for_flags = auto.get("scenario") if template_id == (auto.get("template") or "") else ""

    text, composed_by = llm.compose(
        template_id,
        TEMPLATES[template_id],
        fields,
        comments,
        llm_config(db),
        seized_eur=seized_eur,
        scenario=scenario_for_flags or "",
    )
    subject = str(decisions.get("subject") or "").strip() or build_subject_for_template(
        template_id, fields)

    manual_template = bool(auto.get("template")) and template_id != auto.get("template")
    return {
        "declaration": {
            "template": template_id,
            "kind": template_kind(template_id),
            "text": text,
            "subject": subject,
            "composed_by": composed_by,
        },
        "warnings": validate_decisions(decisions, context, auto),
        "manual_template": manual_template,
    }
