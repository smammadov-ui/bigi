"""Step 1 (identify + confirm the account) + Step 2 (account-status bucket).

MERGED implementation (mini's identification UX + the engine's confirmation):

  - Identification order (stop at the first UNIQUE hit):
      1. ``manual_uuid`` (operator-selected/typed)            -> identified_by="manual"
      2. ``fields["company_uuid"]`` (definitive/potential)    -> "ticket_uuid"
      3. ``fields["company_uuid_candidates"]`` (several ticket UUIDs):
         wallet-IBAN ownership disambiguation                 -> "wallet_iban";
         zero/several owners -> candidates go to the PICKER (needs_selection)
      4. cstools search: register_number -> seized_iban -> debtor_name.
         One hit -> resolved. Several hits: an EXACT key match (regNumber/IBAN)
         wins, else the candidates go to the picker (never guess a lookalike).
      5. Nothing anywhere -> NO_MATCH (Scenario 4; manual UUID entry stays
         available in the UI).

  - Confirmation (match priority, CHECKS_ALGORITHM.md Step 1.4): IBAN match
    wins -> Company requires an address (postcode) match -> Freelancer requires
    address OR DOB. Confirmation is what keeps a lookalike search hit from
    becoming a wrong-debtor declaration.

  - A physical-person request against a Company account is PERSON_VS_COMPANY
    (-> Scenario S5).

  - Step 2 buckets ``cstools`` accountStatus into OPEN / CLOSED / CLOSING /
    RESTRICTED / ONBOARDING / UNKNOWN.

Ambiguity is an OPERATOR decision: when candidates need selection the outcome
is ``None`` (no scenario is resolved and no document is produced until the
operator picks or enters a UUID — the pipeline surfaces the candidates).
"""
from __future__ import annotations

import re

from .bo_client import BOError
from .schemas import AccountStatusBucket, MatchOutcome

# Step 2 — accountStatus -> bucket (CHECKS_ALGORITHM.md Step 2)
_OPEN = {"AccountOpened"}
_CLOSED = {"AccountClosed"}
_ONBOARDING = {
    "registrationStarted", "accountOpeningStarted", "ApplicationInProgress",
    "ApplicationApproved", "ApplicationApprovedByPartnerBank", "DataCollection",
    "KycInfoRequest", "KycCheckRequested", "WaitingIdentityVerification",
    "WaitingOnfidoResolution", "IdentityVerificationError", "legalrepsIndicated",
    "businessAddressConfirmed", "businessDetailsConfirmed", "URWaitingForRegistration",
    "SepaInAwaiting", "SepaInValidationRequested", "SepaInValidationError",
    "AccountDeclined", "AccountDeclineRequested", "None", "NonActive",
}
_CLOSING = {
    "ClosureScheduled", "ClosureInProgress", "WaitingForRecalls",
    "WithdrawalOfFunds", "WithdrawalOutdated",
}
_RESTRICTED = {"LimitedAccount", "AccountBlocked"}

_SPLIT_RE = re.compile(r"[,;\s]+")


def status_bucket(account_status: str) -> str:
    s = (account_status or "").strip()
    if s in _OPEN:
        return AccountStatusBucket.OPEN.value
    if s in _CLOSED:
        return AccountStatusBucket.CLOSED.value
    if s in _CLOSING:
        return AccountStatusBucket.CLOSING.value
    if s in _RESTRICTED:
        return AccountStatusBucket.RESTRICTED.value
    if s in _ONBOARDING:
        return AccountStatusBucket.ONBOARDING.value
    return AccountStatusBucket.UNKNOWN.value


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().casefold())


def norm_reg(s) -> str:
    return re.sub(r"\s+", "", str(s or "").strip()).upper()


def _norm_iban(s) -> str:
    """IBANs compare space-insensitively and case-insensitively."""
    return re.sub(r"\s+", "", str(s or "")).upper()


def _postcodes(s) -> set[str]:
    return set(re.findall(r"\b\d{5}\b", str(s or "")))


def _addr_match(debtor_addr: str, account_addr: str) -> bool:
    """Address match = both carry a 5-digit postcode and they intersect.

    Postcode is the reliable discriminator; if the debtor address has none we
    cannot confirm a match (-> NON-match), which is the safe default.
    """
    a, b = _postcodes(debtor_addr), _postcodes(account_addr)
    return bool(a and b and (a & b))


def is_physical_person(parsed: dict) -> bool:
    """A request against a physical person = DOB present AND no register number."""
    return bool((parsed.get("debtor_dob") or "").strip()) and not (parsed.get("debtor_register_number") or "").strip()


def _flatten_address(addr) -> str:
    if isinstance(addr, dict):
        return " ".join(str(v) for v in addr.values() if isinstance(v, (str, int)))
    return str(addr or "")


def _harvest_strings(o, out: list[str]) -> None:
    """Collect every non-empty string under ``values`` / ``value`` keys in a subtree."""
    if isinstance(o, dict):
        for key in ("values", "value"):
            v = o.get(key)
            if isinstance(v, (list, tuple)):
                out.extend(str(x) for x in v if isinstance(x, (str, int)) and str(x).strip())
            elif isinstance(v, (str, int)) and str(v).strip():
                out.append(str(v))
        for v in o.values():
            _harvest_strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _harvest_strings(v, out)


def _cdd_dob(cdd: dict) -> str:
    """Best-effort PersonBirthdate out of the nested cdd_profile (or flat stub).

    The real profile nests ``sections -> subSections -> parameters -> items ->
    values``: the node carrying ``parameter == "PersonBirthdate"`` may hold its
    values on CHILD items rather than on itself, so once such a node is found
    its whole subtree is harvested.
    """
    if not isinstance(cdd, dict):
        return ""
    if cdd.get("PersonBirthdate"):
        return str(cdd["PersonBirthdate"])
    if cdd.get("dob"):
        return str(cdd["dob"])
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("parameter") == "PersonBirthdate":
                _harvest_strings(o, found)   # values may sit on child items
            else:
                for v in o.values():
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(cdd)
    return next((f for f in found if f.strip()), "")


def _candidate(it: dict) -> dict:
    return {
        "id": it.get("id"),
        "businessName": it.get("businessName", ""),
        "regNumber": it.get("regNumber", ""),
    }


def _item_from_short_info(si: dict, company_uuid: str) -> dict:
    """Normalize a cstools short-info response into the account-status ``item``
    shape. accountStatus comes from status.accountStatus (fallback
    paymentAccountStatus.status). No status-change date -> account_status_updated
    is best-effort: a closed account with no date resolves to S3 (closed-before),
    never spuriously routed out."""
    si = si or {}
    status = si.get("status") or {}
    pay = si.get("paymentAccountStatus") or {}
    return {
        "id": si.get("id") or company_uuid,
        "businessName": si.get("businessName", ""),
        "accountStatus": status.get("accountStatus") or pay.get("status") or "",
        "accountStatusUpdated": si.get("accountStatusUpdated", ""),
        "type": si.get("type", ""),
    }


def _account_item(client, company_uuid: str, seized_iban: str) -> dict:
    """Account-status ``item`` for a KNOWN company, resilient to either endpoint
    being down for it. Prefer the stable short-info GET; if it is access-gated
    (e.g. HTTP 406 "Access is not allowed") or otherwise fails, fall back to the
    cstools_search POST. If BOTH fail the error propagates to the caller."""
    try:
        item = _item_from_short_info(client.cstools_short_info(company_uuid), company_uuid)
        if item.get("accountStatus"):
            return item
    except BOError:
        pass  # short-info unavailable/gated for this company -> fall back to search
    s_items = client.cstools_search(seized_iban or company_uuid).get("items") or []
    return next((it for it in s_items if it.get("id") == company_uuid),
                (s_items[0] if s_items else {}))


def _ticket_ibans(parsed: dict) -> list[str]:
    """The ticket's IBANs (seized first, then the debtor list), normalised."""
    ibans = [_norm_iban(parsed.get("seized_iban"))]
    ibans += [_norm_iban(p) for p in _SPLIT_RE.split(str(parsed.get("debtor_ibans") or ""))]
    return [i for i in ibans if i]


def _pick_search_hit(items: list[dict], kind: str, term: str):
    """From several search candidates, ONLY an exact key match (regNumber/IBAN)
    may auto-resolve; otherwise return None and let the operator pick."""
    if len(items) == 1:
        return items[0]
    if kind == "register_number":
        t = norm_reg(term)
        hits = [it for it in items if norm_reg(it.get("regNumber")) == t]
        return hits[0] if len(hits) == 1 else None
    if kind == "iban":
        t = _norm_iban(term)
        hits = [it for it in items if _norm_iban(it.get("iban")) == t]
        return hits[0] if len(hits) == 1 else None
    return None


def _lookup_name(client, uuid: str) -> str:
    """Best-effort businessName for a known UUID (cstools text search)."""
    try:
        data = client.cstools_search(uuid)
    except BOError:
        return ""
    for it in data.get("items", []) or []:
        if it.get("id") == uuid:
            return it.get("businessName", "")
    return ""


def _base(**over) -> dict:
    out = {
        # identification (mini's shape, kept for the frontend)
        "company_uuid": "", "business_name": "", "matched_by": "none",
        "identified_by": None, "candidates": [], "needs_selection": False,
        "error": None,
        # confirmation + status
        "outcome": None, "account_type": "", "account_status": "",
        "status_bucket": AccountStatusBucket.UNKNOWN.value,
        "account_status_updated": "", "account_address": "", "dob": "",
        "ibans": [], "reasons": [],
        # reused downstream (balance) — one wallets call, used twice.
        # wallets_error != None means the read FAILED (balance unknown, not 0).
        "wallets_items": [], "wallets_error": None,
        "seized_iban": "", "seized_iban_source": None, "main_wallet": None,
    }
    out.update(over)
    return out


def _disambiguate_candidates(client, parsed: dict, cand_uuids: list[str], reasons: list[str]):
    """Mini's wallet-IBAN disambiguation: among several candidate UUIDs the one
    whose BO wallets own the ticket's seized/debtor IBAN is the debtor's account.
    Returns ``(resolved_uuid, error)`` — resolved_uuid is "" when zero/several
    owners, no ticket IBAN, or BO failures (candidates go to the picker)."""
    ibans = _ticket_ibans(parsed)
    errors: list[str] = []
    matches: list[str] = []
    if ibans:
        for uuid in cand_uuids:
            try:
                data = client.wallets(uuid)
            except BOError as exc:
                errors.append(str(exc))
                continue
            wallet_ibans = {_norm_iban(w.get("iban")) for w in data.get("items", []) or []}
            if any(i in wallet_ibans for i in ibans):
                matches.append(uuid)

    if len(matches) == 1:
        reasons.append("multiple candidate UUIDs -> resolved by wallet-IBAN ownership")
        return matches[0], None

    if not ibans:
        why = "no seized/debtor IBAN on the ticket to compare wallets against"
    elif len(matches) > 1:
        why = "several candidate companies own the ticket's IBAN"
    else:
        why = "no candidate company's wallets carry the ticket's IBAN"
    error = f"ticket carries multiple company UUIDs; {why}"
    if errors:
        error += f" ({'; '.join(errors)})"
    return "", error


def _identify(client, parsed: dict, manual_uuid: str | None, reasons: list[str]) -> dict:
    """Resolve the company UUID (or candidates). Returns a partial account dict
    with company_uuid / identified_by / business_name OR candidates+needs_selection.
    Raises BOError only when identification itself cannot run (search down)."""
    # 1. Operator-supplied UUID always wins (no selection needed).
    if manual_uuid:
        reasons.append("operator-selected account (manual/candidate pick)")
        return {"company_uuid": manual_uuid, "identified_by": "manual",
                "business_name": _lookup_name(client, manual_uuid)}

    # 2. UUID carried by the ticket (a single definitive/potential match).
    ticket_uuid = (parsed.get("company_uuid") or "").strip()
    if ticket_uuid:
        return {"company_uuid": ticket_uuid, "identified_by": "ticket_uuid",
                "business_name": ""}

    # 3. Several UUIDs on the ticket -> wallet-IBAN disambiguation.
    cand_uuids = [u for u in _SPLIT_RE.split(str(parsed.get("company_uuid_candidates") or "")) if u]
    if len(cand_uuids) > 1:
        resolved, error = _disambiguate_candidates(client, parsed, cand_uuids, reasons)
        if resolved:
            return {"company_uuid": resolved, "identified_by": "wallet_iban",
                    "business_name": _lookup_name(client, resolved)}
        candidates = [
            {"id": u, "businessName": _lookup_name(client, u), "regNumber": ""}
            for u in cand_uuids
        ]
        return {"company_uuid": "", "candidates": candidates,
                "needs_selection": True, "error": error}

    # 4. Search terms in priority order.
    for kind, term in (("register_number", norm_reg(parsed.get("debtor_register_number"))),
                       ("iban", (parsed.get("seized_iban") or "").strip()),
                       ("name", (parsed.get("debtor_name") or "").strip())):
        if not term:
            continue
        items = client.cstools_search(term).get("items") or []
        if not items:
            continue  # 0 items -> try the next term
        hit = _pick_search_hit(items, kind, term)
        if hit is not None and (hit.get("id") or "").strip():
            reasons.append(f"identified via cstools_search by {kind}")
            return {"company_uuid": (hit.get("id") or "").strip(),
                    "identified_by": kind,
                    "business_name": hit.get("businessName", ""),
                    "_search_item": hit}
        # Several inexact candidates -> the operator picks (never guess).
        return {"company_uuid": "", "candidates": [_candidate(it) for it in items],
                "needs_selection": True, "error": None}

    # 5. Nothing resolved anywhere -> NO_MATCH territory (Scenario 4).
    return {"company_uuid": ""}


def match_account(client, parsed: dict, manual_uuid: str | None = None) -> dict:
    """Run Step 1+2; see module docstring. Never returns a guessed account.

    Outcomes: MATCH / NO_MATCH / PERSON_VS_COMPANY, or ``outcome=None`` with
    ``needs_selection=True`` (operator must pick from ``candidates`` or enter a
    UUID). Raises :class:`BOError` only when identification itself cannot run.
    """
    parsed = parsed or {}
    reasons: list[str] = []
    seized_iban = (parsed.get("seized_iban") or "").strip()

    ident = _identify(client, parsed, (manual_uuid or "").strip() or None, reasons)
    company_uuid = ident.get("company_uuid") or ""

    if ident.get("needs_selection"):
        return _base(candidates=ident.get("candidates") or [], needs_selection=True,
                     error=ident.get("error"), reasons=reasons, seized_iban=seized_iban)

    if not company_uuid:
        reasons.append("no company candidate (searched register number / IBAN / name)")
        return _base(outcome=MatchOutcome.NO_MATCH.value, reasons=reasons,
                     seized_iban=seized_iban)

    # --- gather account data (read-only) --------------------------------------
    item = ident.get("_search_item") or _account_item(client, company_uuid, seized_iban)
    try:
        overview = client.cstools_overview(company_uuid) or {}
    except BOError as exc:
        overview = {}
        reasons.append(f"overview unavailable ({exc.status_code or 'transport'})")
    wallets_error = None
    try:
        wallets_resp = client.wallets(company_uuid) or {}
    except BOError as exc:
        wallets_resp = {}
        wallets_error = str(exc)
        reasons.append(f"wallets unavailable ({exc.status_code or 'transport'})")
    wallets_items = wallets_resp.get("items") or []
    try:
        cdd = client.cdd_profile(company_uuid) or {}
    except BOError as exc:
        cdd = {}
        reasons.append(f"cdd-profile unavailable ({exc.status_code or 'transport'})")

    account_status = item.get("accountStatus", "") if isinstance(item, dict) else ""
    account_status_updated = item.get("accountStatusUpdated", "") if isinstance(item, dict) else ""
    account_type = overview.get("type") or (item.get("type") if isinstance(item, dict) else "") or ""
    business_name = ((item.get("businessName") if isinstance(item, dict) else "")
                     or ident.get("business_name") or "")
    ibans = [w.get("iban", "") for w in wallets_items if w.get("iban")]
    account_address = _flatten_address(overview.get("address"))
    dob = _cdd_dob(cdd)
    bucket = status_bucket(account_status)

    # Real BO short-info carries the status but NOT accountStatusUpdated. The
    # date decides S3 (closed before the ticket) vs ROUTED_OUT (closed on/after)
    # — for a CLOSED account it is worth one extra search read to get it.
    if bucket == AccountStatusBucket.CLOSED.value and not account_status_updated:
        try:
            term = seized_iban or business_name or company_uuid
            s_items = client.cstools_search(term).get("items") or []
            enriched = next((it for it in s_items if it.get("id") == company_uuid), None)
            if enriched and enriched.get("accountStatusUpdated"):
                account_status_updated = enriched["accountStatusUpdated"]
                reasons.append("accountStatusUpdated enriched via cstools_search (closed account)")
        except BOError:
            reasons.append("accountStatusUpdated unavailable — closed account treated as closed-before-ticket")

    # Resolve the seized IBAN from the account's Main wallet when not provided.
    seized_iban_source = "provided" if seized_iban else None
    main_wallet = None
    if not seized_iban:
        w = _pick_main_wallet(wallets_items)
        if w and w.get("iban"):
            seized_iban = str(w["iban"]).strip()
            seized_iban_source = "main_wallet"
            main_wallet = {"id": w.get("id"), "iban": seized_iban, "name": w.get("name")}
            reasons.append(f"seized_iban derived from the account's {w.get('name', '?')!r} wallet")

    # --- confirmation (match priority) ----------------------------------------
    outcome = MatchOutcome.NO_MATCH.value
    matched_by = "none"
    ticket_provided_iban = (parsed.get("seized_iban") or "").strip()
    norm_ibans = {_norm_iban(i) for i in ibans}
    if ticket_provided_iban and _norm_iban(ticket_provided_iban) in norm_ibans:
        outcome, matched_by = MatchOutcome.MATCH.value, "iban"
        reasons.append("IBAN match (overrides name/address)")
    elif account_type == "Company":
        if _addr_match(parsed.get("debtor_address", ""), account_address):
            outcome, matched_by = MatchOutcome.MATCH.value, "address"
            reasons.append("Company: address (postcode) match")
        else:
            reasons.append("Company: address mismatch -> NO_MATCH")
    elif account_type == "Freelancer":
        if _addr_match(parsed.get("debtor_address", ""), account_address):
            outcome, matched_by = MatchOutcome.MATCH.value, "address"
            reasons.append("Freelancer: address match")
        elif (parsed.get("debtor_dob") or "").strip() and _norm(parsed["debtor_dob"]) == _norm(dob):
            outcome, matched_by = MatchOutcome.MATCH.value, "dob"
            reasons.append("Freelancer: DOB match")
        else:
            reasons.append("Freelancer: address AND DOB mismatch -> NO_MATCH")
    else:
        reasons.append(f"unknown account type {account_type!r} -> NO_MATCH")

    # --- person-vs-company override (Scenario 5) -------------------------------
    if outcome == MatchOutcome.MATCH.value and is_physical_person(parsed) and account_type == "Company":
        outcome = MatchOutcome.PERSON_VS_COMPANY.value
        reasons.append("request is a physical person but the account is a Company -> PERSON_VS_COMPANY")

    return _base(
        company_uuid=company_uuid, business_name=business_name,
        matched_by=matched_by, identified_by=ident.get("identified_by"),
        outcome=outcome, account_type=account_type, account_status=account_status,
        status_bucket=bucket, account_status_updated=account_status_updated,
        account_address=account_address, dob=dob, ibans=ibans, reasons=reasons,
        wallets_items=wallets_items, wallets_error=wallets_error, seized_iban=seized_iban,
        seized_iban_source=seized_iban_source, main_wallet=main_wallet,
    )


def _pick_main_wallet(wallets_items: list[dict]):
    """The account's Main wallet: prefer a wallet literally named 'Main', else
    the first wallet with an IBAN."""
    for w in wallets_items or []:
        if str(w.get("name", "")).strip().lower() == "main" and w.get("iban"):
            return w
    return next((w for w in wallets_items or [] if w.get("iban")), None)
