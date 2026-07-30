"""Step 5: resolve one of the 11 coded scenarios + its plan (READ-ONLY).

Ported from the feizure engine's resolver (which merged the full app's
11-scenario logic with mini's correctness fixes):

1. The Processing count it sees is the FILTERED one (own-case + junior
   competitors removed by :mod:`app.checks`) — this ticket's own, already
   submitted seizure never flips S1 -> S2 or contaminates S6A's covered test.
2. Degradation policy: when a check the decision depends on was ``assumed``
   (BO read failed), an undecidable case is not silently resolved — CLOSING
   with unknown balance routes to the operator, and the pipeline flags any
   scenario decided on assumed data for review.
3. Resolution order is explicit: open alerts -> match outcome -> status
   bucket -> seizures/balance.

bigi never creates seizures — the plan carries no create action. For S1/S2 the
declaration presumes this ticket's seizure already exists in BO (the "own
case"); a missing one is surfaced upstream as ``own_case_missing``.
"""
from __future__ import annotations

from .schemas import AccountStatusBucket, MatchOutcome, Scenario

# scenario -> (template, action)
PLAN = {
    Scenario.S1: ("T1", "letter"),
    Scenario.S2: ("T2", "letter"),
    Scenario.S3: ("T6", "letter"),
    Scenario.S4_NO_IBAN: ("T7", "email"),
    Scenario.S4_IBAN: ("T8", "email"),
    Scenario.S5: ("T9", "email"),
    Scenario.S6A: ("T10", "email"),
    Scenario.S6B: ("T11", "email"),
    Scenario.INSOLVENCY: ("T4", "email"),
    Scenario.RFI: ("T5", "data_gathering"),
    Scenario.ROUTED_OUT: ("", "operator"),
}

_RATIONALE = {
    Scenario.S1: "Match, no open alerts, no competing Processing seizure → normal TPD (T1).",
    Scenario.S2: "Match with ≥1 competing Processing seizure → T2 with Bestehende Pfändungen.",
    Scenario.S3: "Account closed/onboarding → T6 (Kundenbeziehung: Nein).",
    Scenario.S4_NO_IBAN: "No match, no IBAN provided → T7 (ask for IBAN).",
    Scenario.S4_IBAN: "No match, IBAN provided but unknown → T8 (ask for correct IBAN).",
    Scenario.S5: "Request against a physical person but the account is a Company → T9 (attach the received document).",
    Scenario.S6A: "Closing and covered (Processing seizure or zero balance) → T10.",
    Scenario.S6B: "Closing with available balance, no Processing seizure → T11 — only the remaining balance can be transferred (handled in BO, not by bigi).",
    Scenario.INSOLVENCY: "Open MNL21 (insolvency) → T4 email; the seizure cannot be processed while insolvency runs.",
    Scenario.RFI: "Open MNL22 (information request) → T5: gather the requested data, no seizure, no §840 letter.",
    Scenario.ROUTED_OUT: "Restricted account, other open alert, or undecidable on degraded data → operator review (no customer document).",
}


def resolve_scenario(match: dict, checks: dict, parsed: dict) -> tuple[str, list[str]]:
    """Decide the scenario from alerts -> match -> status -> balance.

    ``checks`` = {"alerts": {...}, "seizures": {...}, "balance": {...}} as built
    by :mod:`app.checks`. Returns ``(scenario_value, notes)`` where notes
    explain any degradation-driven choice.
    """
    notes: list[str] = []
    alerts = checks.get("alerts") or {}
    seizure_check = checks.get("seizures") or {}
    balance = checks.get("balance") or {}

    # Step 3 — open-alert branches first
    rules = set(alerts.get("open_rules") or [])
    if "MNL21" in rules:
        return Scenario.INSOLVENCY.value, notes
    if "MNL22" in rules:
        return Scenario.RFI.value, notes
    if rules - {"MNL20", "MNL21", "MNL22"}:
        return Scenario.ROUTED_OUT.value, notes  # other open alert -> operator review

    outcome = match.get("outcome")
    if outcome == MatchOutcome.PERSON_VS_COMPANY.value:
        return Scenario.S5.value, notes
    if outcome == MatchOutcome.NO_MATCH.value:
        sc = Scenario.S4_IBAN.value if (parsed.get("seized_iban") or "").strip() else Scenario.S4_NO_IBAN.value
        return sc, notes

    # MATCH — branch by account status bucket
    bucket = match.get("status_bucket")
    if bucket == AccountStatusBucket.ONBOARDING.value:
        return Scenario.S3.value, notes
    if bucket == AccountStatusBucket.CLOSED.value:
        # Only "closed BEFORE the ticket" is S3; closed on/after the receipt
        # date needs manual handling. ISO dates compare lexically.
        closed = (match.get("account_status_updated") or "")[:10]
        received = (parsed.get("date_received") or "")[:10]
        if closed and received and closed >= received:
            notes.append("account closed on/after the ticket receipt date")
            return Scenario.ROUTED_OUT.value, notes
        return Scenario.S3.value, notes
    if bucket == AccountStatusBucket.RESTRICTED.value:
        notes.append(f"restricted account status {match.get('account_status')!r}")
        return Scenario.ROUTED_OUT.value, notes

    processing_count = int(seizure_check.get("processing_count") or 0)
    seizures_assumed = bool(seizure_check.get("assumed"))

    if bucket == AccountStatusBucket.CLOSING.value:
        available = balance.get("available_eur")
        if seizures_assumed or available is None:
            # S6A vs S6B splits on data we don't have — deciding it on assumed
            # data would put the wrong figure in a legal document.
            notes.append("CLOSING account but seizures/balance unavailable → operator review")
            return Scenario.ROUTED_OUT.value, notes
        if balance.get("non_eur"):
            notes.append("non-EUR wallets excluded from available_eur — operator should verify coverage")
        covered = processing_count > 0 or float(available) <= 0
        return (Scenario.S6A.value if covered else Scenario.S6B.value), notes

    # OPEN (or UNKNOWN-but-matched) bucket
    if seizures_assumed:
        notes.append("seizure listing unavailable — S1/S2 split assumed (S1); verify before sending")
    if processing_count > 0:
        return Scenario.S2.value, notes
    return Scenario.S1.value, notes


def build_plan(scenario: str, notes: list[str] | None = None) -> dict:
    sc = Scenario(scenario)
    template, action = PLAN[sc]
    return {
        "scenario": scenario,
        "template": template,
        "action": action,           # letter | email | data_gathering | operator
        "rationale": _RATIONALE[sc],
        "notes": list(notes or []),
    }
