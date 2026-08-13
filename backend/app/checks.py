"""Steps 3-4: open alerts, ongoing-seizure check, balance (all read-only).

Carries mini's correctness discipline into the 11-scenario pipeline:

* **Own-case filtering** (``same_case``): a Processing seizure whose
  ``caseNumber`` matches the ticket's own case reference IS this ticket's case
  (submitted before the TPD is written) — it must not flip S1->S2, contaminate
  S6A's covered test, or be declared as a competing prior seizure. Reported
  separately in ``ignored_same_case``; its BO ``seizedAmount`` is the
  authoritative "derzeit gepfändet" figure for the letter (see ``amounts``).
* **Junior filtering**: a competing Processing seizure created strictly AFTER
  the ticket's own case is junior and must not be declared. Reported in
  ``ignored_later``. Only provably-later rows are dropped.
* **Structured German descriptions**: T2 bullets are built from BO's structured
  fields (issuedBy / amount / issuedOn), not the free-text comment (Porters
  stubs, Jira smart links). ``strip_jira_links`` scrubs the fallback.
* **EUR-only balance**: no FX guessing in a legal figure. Non-EUR wallets are
  reported separately for the operator.
* **Degradation**: read failures never raise out of this module — they return
  ``assumed``/``error`` markers the pipeline turns into warnings (and the
  scenario resolver treats conservatively: undecidable CLOSING cases route to
  the operator).
"""
from __future__ import annotations

import re

from .bo_client import BOError, is_processing, is_settling
from .formatting import de_amount, de_date

# ---------------------------------------------------------------------------
# Alerts (Step 3)
# ---------------------------------------------------------------------------


# BO rule codes come in variants ("MNL21", "MNL-21-FP", "MNL 21"). The decision
# tree keys on the canonical "MNL<N>" form; anything non-MNL stays as-is (and
# therefore routes to the operator as an unknown open alert).
_MNL_RE = re.compile(r"MNL[\s_-]*(\d+)", re.IGNORECASE)


def canonical_rule(rule) -> str:
    """"MNL-21-FP" / "mnl 21" -> "MNL21"; unknown formats pass through."""
    m = _MNL_RE.search(str(rule or ""))
    return f"MNL{m.group(1)}" if m else str(rule or "").strip()


def open_alert_rules(alerts_items: list[dict]) -> set[str]:
    """CANONICAL rule codes of OPEN alerts (resolvedOn is null)."""
    rules: set[str] = set()
    for a in alerts_items or []:
        if a.get("resolvedOn") is None:  # open
            r = a.get("rules")
            if isinstance(r, (list, tuple)):
                rules.update(canonical_rule(x) for x in r)
            elif r:
                rules.add(canonical_rule(r))
    return {r for r in rules if r}


def check_alerts(client, company_uuid: str) -> dict:
    """Return ``{items, open_rules, error, assumed}``; degrades on BO failure."""
    if not company_uuid:
        return {"items": [], "open_rules": [], "error": None, "assumed": False}
    try:
        resp = client.get_alerts(company_uuid)
    except BOError as exc:
        # Alerts gate INSOLVENCY/RFI/ROUTED_OUT. Unknown alerts = the resolver
        # cannot rule those out — surfaced as `assumed` so the operator sees
        # the scenario may be wrong.
        return {"items": [], "open_rules": [], "error": str(exc), "assumed": True}
    items = resp.get("items") or []
    return {"items": items, "open_rules": sorted(open_alert_rules(items)),
            "error": None, "assumed": False}


# ---------------------------------------------------------------------------
# Comment hygiene + case-number matching (mini)
# ---------------------------------------------------------------------------

# Jira smart-link markup: [text|url] -> keep "text"
_SMARTLINK_RE = re.compile(r"\[([^\[\]|]+)\|[^\[\]]+\]")
# bare http(s) URLs
_BARE_URL_RE = re.compile(r"https?://\S+")


def strip_jira_links(text) -> str:
    """Remove Jira link markup from a comment, keeping human-readable text."""
    s = str(text or "")
    s = _SMARTLINK_RE.sub(r"\1", s)
    s = _BARE_URL_RE.sub("", s)
    s = re.sub(r"  +", " ", s)  # collapse leftover double spaces
    return s.strip()


# Case-number matching: normalise away formatting (spaces, dots, slashes, dashes,
# case) so "2614/239/24045 - VO 05 - 12619/26 F" and "261423924045VO051261926F"
# compare equal. Only alphanumerics are kept.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_MIN_MATCH_LEN = 6  # guard against trivially-short (accidental) matches


def _norm_case(s) -> str:
    return _NON_ALNUM_RE.sub("", str(s or "").lower())


_SEGMENT_RE = re.compile(r"[a-z0-9]+")


def _case_segments(s) -> list[str]:
    """Reference split into alphanumeric segments (separator-aware)."""
    return _SEGMENT_RE.findall(str(s or "").lower())


def same_case(bo_case_number, ticket_case_ref) -> bool:
    """True if a BO ``caseNumber`` refers to the same case as the ticket's ref.

    Matching rules (FPOPCL-31227 hardening — a shared MIDDLE segment must not
    match: ``82611302`` is AOK's customer number inside
    ``V00001726483-82611302-57223`` and is common to ALL of that creditor's
    cases against the debtor):

    1. Full normalized equality (alphanumerics only, lowercased) — covers
       formatting variants ("2614/239…F" == "261423924045…F").
    2. EDGE containment on segment boundaries: the shorter ref's complete
       segment sequence is the PREFIX or SUFFIX of the longer one — covers a
       ticket carrying only the court tail ("12619/26 F" ⊂ "… - VO 05 -
       12619/26 F") and a full ``case_reference:`` value inside Porters'
       "document_id: … | case_reference: …" combo.
    3. Collapsed-string edge fallback for LONG refs (>= 10 chars) — covers BO
       values stored without separators.

    A middle-of-string hit never matches. Guarded by a min length so short
    fragments can't match by accident. Empty ref -> never matches.
    """
    b = _norm_case(bo_case_number)
    t = _norm_case(ticket_case_ref)
    if len(b) < _MIN_MATCH_LEN or len(t) < _MIN_MATCH_LEN:
        return False
    if b == t:
        return True

    b_seg = _case_segments(bo_case_number)
    t_seg = _case_segments(ticket_case_ref)
    shorter, longer = (t_seg, b_seg) if len(t) <= len(b) else (b_seg, t_seg)
    n = len(shorter)
    if n and len(longer) >= n and len("".join(shorter)) >= _MIN_MATCH_LEN:
        if longer[:n] == shorter or longer[-n:] == shorter:
            return True

    shorter_c, longer_c = (t, b) if len(t) <= len(b) else (b, t)
    if len(shorter_c) >= 10 and (longer_c.startswith(shorter_c) or longer_c.endswith(shorter_c)):
        return True
    return False


def seizure_description_de(detail: dict) -> str:
    """German one-liner for the letter's [Comment] bullet, from STRUCTURED fields.

    BO's free-text ``comment`` is unreliable: Porters-created seizures carry only
    the stub "The seizure was created by the Porters" while the real facts
    (creditor, issue date, amount) live in structured fields. Returns "" when
    neither a creditor nor an amount is present (caller falls back to the raw
    comment).
    """
    detail = detail or {}
    creditor = str(
        detail.get("issuedBy")
        or detail.get("creditorName")
        or detail.get("creditorRepresentativeName")
        or ""
    ).strip()
    amount = detail.get("amount")
    if amount is None:
        amount = detail.get("seizureAmount")
    if not creditor and amount is None:
        return ""

    sentence = "Wir haben eine Pfändung"
    if creditor:
        sentence += f" von {creditor}"
    issued = de_date(detail.get("issuedOn"))
    if issued:
        sentence += f", ausgestellt am {issued}"
    business = str(detail.get("businessName") or "").strip()
    if business:
        sentence += f", für {business}"
    sentence += " erhalten."
    if amount is not None:
        sentence += f" Der Pfändungsbetrag beträgt {de_amount(amount)} EUR."
    return sentence


# ---------------------------------------------------------------------------
# Ongoing-seizure check (Step 4) — own-case + junior aware (mini)
# ---------------------------------------------------------------------------


def _assumed_seizures(error: str) -> dict:
    return {
        "processing_count": 0,
        "seizures": [],
        "ignored_same_case": [],
        "ignored_later": [],
        "settling": [],
        "own_case_missing": True,
        "error": error,
        "assumed": True,
    }


def _settling_row(s: dict) -> dict:
    """Compact row for a settling seizure, straight from the LISTING (the
    listing rows carry ``seizedAmount``/``amount`` — no detail read needed;
    verified against real BO for FPOPCL-31278)."""
    return {
        "id": s.get("id"),
        "caseNumber": s.get("caseNumber", ""),
        "status": s.get("status"),
        "created": s.get("created"),
        "seized_amount": s.get("seizedAmount"),
        "claim_amount": s.get("amount"),
    }


def check_ongoing_seizures(client, company_uuid: str, ticket_case_ref: str = "") -> dict:
    """Competing Processing seizures for the S1/S2 + S6A decisions.

    Returns ``{processing_count, seizures, ignored_same_case, ignored_later,
    settling, own_case_missing, error, assumed}``. ``seizures`` are the
    COMPETING prior seizures only — the ticket's own case and provably-junior
    competitors are filtered out (but reported), so:

      - ``processing_count == 0``  -> S1 territory (no Bestehende Pfändungen)
      - ``processing_count >= 1``  -> S2 / S6A-covered territory

    ``settling`` are OTHER seizures in a settling status (see
    ``bo_client.SETTLING_STATUSES``, e.g. ``PendingTransferApproval``): their
    ``seized_amount`` is already captured and merely awaits payout, so the
    scenario resolver subtracts it from the available balance in the S6A/S6B
    coverage test (FPOPCL-31278). They are NOT competing Processing seizures —
    they never flip S1 -> S2. Junior filtering does not apply to them either:
    coverage is about where the money physically is, not about seniority.
    """
    if not company_uuid:
        return _assumed_seizures("account not resolved — ongoing-seizure check skipped")

    try:
        listing = client.list_seizures(company_uuid)
        raw = listing.get("seizures", []) or []
        processing = [s for s in raw if is_processing(s)]
        # Settling seizures (captured funds pending transfer approval) — the
        # ticket's own case is not "someone else's captured money", so it is
        # excluded the same way as in the Processing pass.
        settling = [_settling_row(s) for s in raw
                    if is_settling(s)
                    and not same_case(s.get("caseNumber", ""), ticket_case_ref)]

        seizures: list[dict] = []
        ignored: list[dict] = []
        for s in processing:
            sid = s.get("id")
            detail: dict = {}
            if sid is not None:
                try:
                    detail = client.get_seizure(sid) or {}
                except BOError:
                    detail = {}  # listing row alone still carries id/case/created
            created = detail.get("created", s.get("created"))
            bal_obj = detail.get("balance") or {}
            case_number = detail.get("caseNumber", s.get("caseNumber", ""))
            comment = strip_jira_links(detail.get("comment", s.get("comment", "")))
            row = {
                "id": sid,
                "caseNumber": case_number,
                "status": detail.get("status", s.get("status")),
                "created": created,
                "comment": comment,
                # Letter bullet: structured facts first, raw comment only as a
                # fallback when BO carries no creditor/amount for the seizure.
                "description_de": seizure_description_de(detail) or comment,
                # Money held by the seizure lives here, NOT on the bank wallets
                # endpoint: seizedAmount = captured so far; balance.clientTotal =
                # freely available; amount = the seizure's claim.
                "seized_amount": detail.get("seizedAmount"),
                "claim_amount": detail.get("amount"),
                "client_total": bal_obj.get("clientTotal"),
            }
            # The ticket's own case (already submitted in BO) is not a
            # competing seizure.
            if same_case(case_number, ticket_case_ref):
                ignored.append(row)
            else:
                seizures.append(row)
    except BOError as exc:
        return _assumed_seizures(str(exc))

    # Ignore competing Processing seizures created AFTER this ticket's own case.
    # The current case's reference point is its own (ignored_same_case) seizure
    # in BO; any competing seizure whose ``created`` is strictly later is junior
    # to this case and must not be declared. When several own-case rows exist we
    # take the latest ``created`` (the safer cutoff — it drops fewer competitors).
    # With no own-case seizure there is no cutoff, so nothing is filtered here
    # (the missing own case is surfaced as ``own_case_missing``).
    ignored_later: list[dict] = []
    own_created = [str(s.get("created")) for s in ignored if s.get("created")]
    if own_created:
        cutoff = max(own_created)
        kept: list[dict] = []
        for s in seizures:
            created = str(s.get("created") or "")
            # Only drop when we can positively prove it is later than the cutoff;
            # an unknown/empty created is kept (cannot be shown to be junior).
            if created and created > cutoff:
                ignored_later.append(s)
            else:
                kept.append(s)
        seizures = kept

    seizures.sort(key=lambda s: str(s.get("created") or ""))
    ignored_later.sort(key=lambda s: str(s.get("created") or ""))
    settling.sort(key=lambda s: str(s.get("created") or ""))
    return {
        "processing_count": len(seizures),
        "seizures": seizures,
        "ignored_same_case": ignored,
        # Settling (e.g. PendingTransferApproval) seizures other than the own
        # case — captured funds the S6A/S6B coverage test must subtract.
        "settling": settling,
        # Competing Processing seizures dropped for being created after this
        # ticket's own case — surfaced so the operator sees what was excluded.
        "ignored_later": ignored_later,
        "own_case_missing": not ignored,
        "error": None,
        "assumed": False,
    }


# ---------------------------------------------------------------------------
# Balance (EUR-only, mini)
# ---------------------------------------------------------------------------


def account_balance(wallets_items: list[dict] | None, *, error: str | None = None) -> dict:
    """Sum EUR wallet balances; report non-EUR wallets separately (NO FX).

    Consumes the wallet list already fetched during matching (one BO call, used
    twice). Returns ``{available_eur, available_eur_de, breakdown, non_eur,
    error}``; ``available_eur`` is None when the wallets could not be read.
    """
    base = {"available_eur": None, "available_eur_de": None, "breakdown": [], "non_eur": []}
    if error:
        return {**base, "error": error}

    total = 0.0
    breakdown: list[dict] = []
    non_eur: list[dict] = []
    for w in wallets_items or []:
        currency = (w.get("currency") or "EUR").strip().upper()
        try:
            bal = float(w.get("balance", 0) or 0)
        except (TypeError, ValueError):
            bal = 0.0
        row = {"iban": w.get("iban"), "name": w.get("name"),
               "balance": bal, "currency": currency}
        if currency == "EUR":
            total += bal
            breakdown.append(row)
        else:
            non_eur.append(row)

    total = round(total, 2)
    return {
        "available_eur": total,
        "available_eur_de": de_amount(total),
        "breakdown": breakdown,
        "non_eur": non_eur,
        "error": None,
    }


def held_funds(seizure_check: dict) -> dict:
    """Funds held under seizures (own case + competing Processing + settling) —
    they live on the seizure record, not the wallets endpoint (which reads ~0
    for them). Returns ``{held_eur, held_eur_de, client_total_eur,
    client_total_eur_de}``.
    """
    all_rows = ((seizure_check.get("seizures") or [])
                + (seizure_check.get("ignored_same_case") or [])
                + (seizure_check.get("settling") or []))
    held = round(sum(float(s.get("seized_amount") or 0) for s in all_rows), 2)
    client_total = next(
        (s.get("client_total") for s in all_rows if s.get("client_total") is not None),
        None,
    )
    return {
        "held_eur": held,
        "held_eur_de": de_amount(held) if held else None,
        "client_total_eur": client_total,
        "client_total_eur_de": de_amount(client_total) if client_total is not None else None,
    }
