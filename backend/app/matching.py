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

import difflib
import re

from .addresses import MISMATCH, STRONG, UNKNOWN, WEAK, compare_addresses
from .bo_client import BOError
from .formatting import iso_date_any
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
    """A request against a physical person = DOB present AND no register number
    AND the debtor name carries no company legal form.

    Porters sometimes fills the legal representative's date of birth on a
    COMPANY debtor ticket (live: FPOPCL-31103, "Magcars UG (haftungs-
    beschränkt)" with a DOB) — a company cannot have a birthday, so a legal
    form in the name vetoes the DOB heuristic.
    """
    if not (parsed.get("debtor_dob") or "").strip():
        return False
    if (parsed.get("debtor_register_number") or "").strip():
        return False
    name = re.sub(r"\s+", " ", str(parsed.get("debtor_name") or "")).strip()
    if name and _LEGAL_SUFFIX_RE.search(name):
        return False
    return True


def _same_dob(a, b) -> bool:
    """Dates of birth compare on the normalized ISO date — the ticket says
    ``1989-08-21`` while BO's CDD stores ``21.08.1989`` (live case
    FPOPCL-31102). Falls back to plain string equality when either side does
    not parse as a date."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    ia, ib = iso_date_any(a), iso_date_any(b)
    if ia and ib:
        return ia == ib
    return _norm(a) == _norm(b)


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


def _cdd_param_value(cdd: dict, parameter: str, flat_keys: tuple[str, ...] = ()) -> str:
    """Best-effort value of a named parameter out of the nested cdd_profile
    (or a flat stub key).

    The real profile nests ``sections -> subSections -> parameters -> items``:
    the node carrying the ``parameter`` may hold its value on CHILD items or in
    its ``properties`` rather than on itself (live: PersonBirthdate with empty
    ``values`` but ``properties: [{name: "Date of Birth", value: …}]``), so
    once such a node is found its whole subtree is harvested.
    """
    if not isinstance(cdd, dict):
        return ""
    for k in flat_keys:
        if cdd.get(k):
            return str(cdd[k])
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("parameter") == parameter:
                _harvest_strings(o, found)   # values may sit on child items
            else:
                for v in o.values():
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(cdd)
    return next((f for f in found if f.strip()), "")


def _cdd_dob(cdd: dict) -> str:
    """Best-effort PersonBirthdate out of the nested cdd_profile (or flat stub)."""
    return _cdd_param_value(cdd, "PersonBirthdate", ("PersonBirthdate", "dob"))


def _cdd_registered_name(cdd: dict) -> str:
    """Best-effort registered/trade name — counts toward name agreement per the
    analyst identification matrix ("Freelancer register name … match")."""
    return _cdd_param_value(cdd, "CompanyRegisteredName", ("CompanyRegisteredName",))


def _cdd_person_names(cdd: dict) -> list[str]:
    """ALL ``PersonFullName`` values out of the cdd_profile (owner / person
    nodes), deduped. For FREELANCER accounts these count toward name
    agreement: a sole trader's BO businessName is often the TRADE name
    (Geschäftsbezeichnung) while the seizure names the PERSON — live case
    FPOPCL-31366, "Tarkan Öztepe" vs "HLP Druck - Textilveredelung"."""
    if not isinstance(cdd, dict):
        return []
    out: list[str] = []
    flat = cdd.get("PersonFullName")
    if isinstance(flat, str) and flat.strip():
        out.append(flat)
    elif isinstance(flat, (list, tuple)):
        out.extend(str(x) for x in flat if str(x).strip())

    def walk(o):
        if isinstance(o, dict):
            if o.get("parameter") == "PersonFullName":
                found: list[str] = []
                _harvest_strings(o, found)   # first string = the name value
                first = next((f for f in found if f.strip()), "")
                if first:
                    out.append(first)
            else:
                for v in o.values():
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(cdd)
    seen: list[str] = []
    for n in out:
        n = re.sub(r"\s+", " ", str(n)).strip()
        if n and n.casefold() not in (s.casefold() for s in seen):
            seen.append(n)
    return seen


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


# Trailing German legal-form suffixes, stripped to build a broader search term
# ("NOV Energys UG (haftungsbeschränkt)" -> "NOV Energys" — how an operator
# searches). Longest forms first.
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(?:"
    r"UG\s*\(haftungsbeschränkt\)(?:\s*&\s*Co\.?\s*KG)?|"
    r"GmbH\s*&\s*Co\.?\s*KGaA|GmbH\s*&\s*Co\.?\s*KG|"
    r"gGmbH|GmbH|mbH|AG\s*&\s*Co\.?\s*KG|KGaA|AG|UG|SE|"
    r"OHG|KG|GbR|e\.?\s?K\.?|e\.?\s?V\.?|PartG(?:\s*mbB)?|"
    r"Ltd\.?|B\.?V\.?|S\.?à\s?r\.?l\.?"
    r")\s*$",
    re.IGNORECASE,
)


def name_variants(name: str) -> list[str]:
    """Search variants for a company name, most exact first: verbatim,
    whitespace-collapsed, legal-suffix-stripped base (repeatedly). Distinct,
    non-trivial (>= 4 chars) only."""
    out: list[str] = []

    def add(s: str) -> None:
        s = s.strip()
        if len(s) >= 4 and s not in out:
            out.append(s)

    raw = str(name or "").strip()
    add(raw)
    add(re.sub(r"\s+", " ", raw))
    base = re.sub(r"\s+", " ", raw)
    for _ in range(2):  # strip up to two stacked suffixes
        stripped = _LEGAL_SUFFIX_RE.sub("", base).strip()
        if stripped == base:
            break
        base = stripped
        add(base)
    return out


def _same_name(a, b) -> bool:
    """Company names compare equal after whitespace-collapse + casefold —
    covers BO storing 'UG  (haftungsbeschränkt)' with a double space while the
    ticket carries a single one."""
    return bool(_norm(a)) and _norm(a) == _norm(b)


_NAME_XLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                            "Ä": "ae", "Ö": "oe", "Ü": "ue",
                            "é": "e", "è": "e", "à": "a"})


def _legal_form_key(name: str) -> str:
    """Canonical legal-form family carried by a name ('' when none): 'ACME UG
    (haftungsbeschränkt)' -> 'ug', 'ACME gGmbH'/'ACME mbH' -> 'gmbh'."""
    m = _LEGAL_SUFFIX_RE.search(re.sub(r"\s+", " ", str(name or "")).strip())
    if not m:
        return ""
    f = re.sub(r"[^a-z]", "", m.group(0).casefold())
    for prefix, key in (("ughaftungsbeschr", "ug"), ("gmbhcokgaa", "gmbhcokgaa"),
                        ("gmbhcokg", "gmbhcokg"), ("ggmbh", "gmbh"),
                        ("gmbh", "gmbh"), ("mbh", "gmbh"), ("agcokg", "agcokg")):
        if f.startswith(prefix):
            return key
    return f


def _name_key_tokens(name: str) -> list[str]:
    """Comparison tokens: legal suffixes stripped (up to two, stacked), umlauts
    transliterated, casefolded, alphanumeric runs only."""
    s = re.sub(r"\s+", " ", str(name or "")).strip()
    for _ in range(2):
        stripped = _LEGAL_SUFFIX_RE.sub("", s).strip()
        if stripped == s:
            break
        s = stripped
    s = s.translate(_NAME_XLIT).casefold()
    return re.findall(r"[a-z0-9]+", s)


def _names_similar(a: str, b: str) -> bool:
    """Fuzzy-tolerant name agreement per the analyst identification matrix:
    equal base tokens, token containment (a register/trade name that extends
    the main name), or high string similarity (typos). Names carrying
    CONFLICTING explicit legal forms (ACME UG vs ACME GmbH) are different
    legal entities and never agree."""
    fa, fb = _legal_form_key(a), _legal_form_key(b)
    if fa and fb and fa != fb:
        return False
    ta, tb = set(_name_key_tokens(a)), set(_name_key_tokens(b))
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if (ta <= tb or tb <= ta) and any(len(t) >= 3 for t in (ta & tb)):
        return True
    ratio = difflib.SequenceMatcher(
        None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return ratio >= 0.85


def _name_agreement(ticket_name, account_names) -> tuple[bool | None, str]:
    """(True/False/None, detail): does the ticket's debtor name agree with any
    of the account's names? ``None`` = not comparable (a side is missing)."""
    tn = re.sub(r"\s+", " ", str(ticket_name or "")).strip()
    cleaned = [re.sub(r"\s+", " ", str(n or "")).strip() for n in account_names]
    names: list[str] = []
    for n in cleaned:   # dedupe (businessName often equals the registered name)
        if n and n.casefold() not in (s.casefold() for s in names):
            names.append(n)
    if not tn or not names:
        return None, "debtor name or account name missing"
    for n in names:
        if _names_similar(tn, n):
            return True, f"ticket {tn!r} ~ account {n!r}"
    return False, f"ticket {tn!r} vs account {', '.join(repr(n) for n in names)}"


def _pick_search_hit(items: list[dict], kind: str, term: str, full_name: str = ""):
    """From several search candidates, ONLY an exact key match (regNumber/IBAN,
    or the full normalized company name) may auto-resolve; otherwise return
    None and let the operator pick."""
    if kind == "register_number":
        t = norm_reg(term)
        hits = [it for it in items if norm_reg(it.get("regNumber")) == t]
        if len(hits) == 1:
            return hits[0]
    if kind == "iban":
        t = _norm_iban(term)
        hits = [it for it in items if _norm_iban(it.get("iban")) == t]
        if len(hits) == 1:
            return hits[0]
    if kind == "name":
        # A relaxed variant search may return several items; the one whose FULL
        # normalized name equals the ticket's is the exact hit.
        hits = [it for it in items if _same_name(it.get("businessName"), full_name)]
        if len(hits) == 1:
            return hits[0]
    if len(items) == 1:
        return items[0]
    return None


def _disambiguate_same_name(client, parsed: dict, exact_items: list[dict],
                            full_name: str, reasons: list[str]):
    """Several candidates carry EXACTLY the ticket's (normalized) name — often
    duplicate BO records. Use the ticket's address as the locator: grade each
    candidate's overview address (capped); a single STRONG one wins. If several
    are strong (true duplicates share the registered address), the single one
    with an OPEN account wins. Anything else -> annotated picker.

    Returns ``(ident_dict, None)`` when resolved, else ``(None, graded_candidates)``.
    """
    ticket_addr = (parsed.get("debtor_address") or "").strip()
    if not ticket_addr:
        return None, None
    graded: list[tuple[dict, dict]] = []   # (item, address_check)
    for it in exact_items[:6]:             # bounded: one overview call each
        try:
            overview = client.cstools_overview(it.get("id")) or {}
        except BOError:
            graded.append((it, {"grade": UNKNOWN, "detail": "overview unavailable"}))
            continue
        graded.append((it, compare_addresses(ticket_addr, overview.get("address"))))

    strong = [(it, ac) for it, ac in graded if ac["grade"] == STRONG]
    if len(strong) == 1:
        it, ac = strong[0]
        reasons.append(
            f"{len(exact_items)} same-name candidates — disambiguated by address "
            f"({ac['detail']})")
        return ({"company_uuid": (it.get("id") or "").strip(), "identified_by": "name",
                 "name_exact": True, "business_name": it.get("businessName", ""),
                 "_search_item": it}, None)
    if len(strong) > 1:
        open_ones = [(it, ac) for it, ac in strong
                     if status_bucket(it.get("accountStatus", "")) == AccountStatusBucket.OPEN.value]
        if len(open_ones) == 1:
            it, ac = open_ones[0]
            reasons.append(
                f"{len(strong)} same-name candidates share the matching address "
                "(duplicate records) — the only one with an OPEN account chosen")
            return ({"company_uuid": (it.get("id") or "").strip(), "identified_by": "name",
                     "name_exact": True, "business_name": it.get("businessName", ""),
                     "_search_item": it}, None)
        reasons.append(f"{len(strong)} same-name candidates with matching addresses — operator picks")
    else:
        reasons.append("several same-name candidates, none with a matching address — operator picks")
    _rank = {STRONG: 0, WEAK: 1, UNKNOWN: 2, MISMATCH: 3}
    graded.sort(key=lambda pair: _rank.get(pair[1]["grade"], 9))
    graded_candidates = [
        {**_candidate(it),
         "note": f"address: {ac['grade']}"
                 + (f" · {it.get('accountStatus')}" if it.get("accountStatus") else "")}
        for it, ac in graded
    ]
    return None, graded_candidates


def _find_by_name(client, full_name: str, exclude_id=None):
    """Resolve ``full_name`` to a single company via the variant search.

    Returns the item when the name resolves uniquely (one hit, or an exact
    normalized-name hit among several) to a company other than ``exclude_id``;
    else None. BO failures resolve to None — the conflict check is advisory.
    """
    for term in name_variants(full_name):
        try:
            items = client.cstools_search(term).get("items") or []
        except BOError:
            return None
        if not items:
            continue
        hit = _pick_search_hit(items, "name", term, full_name=full_name)
        if hit is not None and (hit.get("id") or "").strip() and hit.get("id") != exclude_id:
            return hit
        return None  # ambiguous or same company -> no conflict pair
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
        "address_check": None,
    }
    out.update(over)
    return out


def _company_exists(client, uuid: str):
    """True / False / None — does BO know this UUID as a company? A 404 is a
    definitive NO (real BO: "Company with ID = … not found" — e.g. a seizure
    entity's UUID from a Porters link). Other failures (gated 406, transient
    5xx) prove nothing -> None, the candidate is kept."""
    try:
        client.cstools_short_info(uuid)
        return True
    except BOError as exc:
        return False if exc.status_code == 404 else None
    except Exception:
        return None


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

    # 3. Several UUIDs on the ticket -> validate against BO, then wallet-IBAN
    # disambiguation. Seizure-link UUIDs are filtered at extraction, but any
    # candidate BO does not know as a company (404) is dropped here as the
    # safety net — a single survivor resolves without the picker.
    cand_uuids = [u for u in _SPLIT_RE.split(str(parsed.get("company_uuid_candidates") or "")) if u]
    if len(cand_uuids) > 1:
        valid = [u for u in cand_uuids if _company_exists(client, u) is not False]
        dropped = [u for u in cand_uuids if u not in valid]
        if dropped:
            reasons.append(
                f"ignored {len(dropped)} ticket UUID(s) BO does not know as a "
                f"company (likely seizure/entity links): {', '.join(dropped)}")
        if len(valid) == 1:
            return {"company_uuid": valid[0], "identified_by": "ticket_uuid",
                    "business_name": _lookup_name(client, valid[0])}
        cand_uuids = valid or cand_uuids
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

    # 4. Search terms in priority order. The name search retries with relaxed
    # variants (whitespace-collapsed, legal-suffix-stripped) — BO's literal
    # search misses e.g. a double space in the stored name, while an operator
    # simply types the base name.
    full_name = (parsed.get("debtor_name") or "").strip()
    searches: list[tuple[str, str]] = []
    reg = norm_reg(parsed.get("debtor_register_number"))
    if reg:
        searches.append(("register_number", reg))
    iban = (parsed.get("seized_iban") or "").strip()
    if iban:
        searches.append(("iban", iban))
    searches.extend(("name", v) for v in name_variants(full_name))

    for kind, term in searches:
        items = client.cstools_search(term).get("items") or []
        if not items:
            continue  # 0 items -> try the next term / variant
        hit = _pick_search_hit(items, kind, term, full_name=full_name)
        if hit is not None and (hit.get("id") or "").strip():
            # IBAN-vs-name conflict check (live case FPOPCL-14753): a creditor
            # may quote an IBAN belonging to a SIBLING company (same owner,
            # different legal entity). When the IBAN hit's business name does
            # not match the ticket's debtor name AND the debtor name resolves
            # to a DIFFERENT company, the debtor's legal identity is ambiguous
            # -> the operator picks; never guess between legal entities.
            if kind == "iban" and full_name and not _same_name(hit.get("businessName"), full_name):
                conflict = _find_by_name(client, full_name, exclude_id=hit.get("id"))
                if conflict is not None:
                    reasons.append(
                        "seized IBAN belongs to a different legal entity than the "
                        "ticket's debtor name — operator must pick the debtor")
                    return {
                        "company_uuid": "",
                        "candidates": [_candidate(hit), _candidate(conflict)],
                        "needs_selection": True,
                        "error": (
                            f"the seized IBAN belongs to {hit.get('businessName', '?')!r}, "
                            f"but the ticket names {full_name!r} as the debtor — "
                            "pick the company the seizure is legally directed at"),
                    }
                reasons.append(
                    f"seized IBAN's account name {hit.get('businessName', '?')!r} does not "
                    "match the ticket's debtor name — verify the debtor entity")
            name_exact = kind == "name" and _same_name(hit.get("businessName"), full_name)
            reasons.append(
                f"identified via cstools_search by {kind}"
                + (f" (variant {term!r})" if kind == "name" and term != full_name else "")
                + (" — full name equality" if name_exact else ""))
            return {"company_uuid": (hit.get("id") or "").strip(),
                    "identified_by": kind,
                    "name_exact": name_exact,
                    "business_name": hit.get("businessName", ""),
                    "_search_item": hit}
        if kind == "name":
            exact = [it for it in items if _same_name(it.get("businessName"), full_name)]
            if len(exact) > 1:
                resolved, graded = _disambiguate_same_name(client, parsed, exact, full_name, reasons)
                if resolved is not None:
                    return resolved
                return {"company_uuid": "",
                        "candidates": graded or [_candidate(it) for it in exact],
                        "needs_selection": True,
                        "error": (f"{len(exact)} companies carry exactly the ticket's name — "
                                  "pick the debtor (address grades shown)")}
        # Several inexact candidates -> the operator picks (never guess).
        # The fuzzy search can return dozens of loose hits; cap the picker.
        shown = [_candidate(it) for it in items[:12]]
        note = None if len(items) <= 12 else \
            f"showing the first 12 of {len(items)} search hits — refine via manual UUID if absent"
        return {"company_uuid": "", "candidates": shown,
                "needs_selection": True, "error": note}

    # 5. Nothing resolved anywhere -> NO_MATCH territory (Scenario 4).
    return {"company_uuid": ""}


def _find_freelancers_by_name(client, full_name: str) -> list[dict]:
    """Freelancer-typed cstools hits for a person's name (spec Q10b lookup).

    Best-effort: tries the name variants until items come back; BO failures
    resolve to an empty list (the S5 override then applies directly).
    """
    for term in name_variants(full_name):
        try:
            items = client.cstools_search(term).get("items") or []
        except BOError:
            return []
        if items:
            return [it for it in items
                    if str(it.get("type", "")).strip().lower() == "freelancer"][:5]
    return []


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
    # Real BO returns accountStatusUpdated as an EPOCH INTEGER (ms); normalize
    # every representation to an ISO date so downstream slicing/compares hold.
    account_status_updated = iso_date_any(
        item.get("accountStatusUpdated", "") if isinstance(item, dict) else "")
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
                account_status_updated = iso_date_any(enriched["accountStatusUpdated"])
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

    # --- confirmation (match priority, graded addresses) -----------------------
    # Address comparison is GRADED (see app.addresses): strong confirms, weak
    # needs a key identity, mismatch rejects — fuzzy similarity can upgrade
    # weak->strong but never rescue a hard mismatch. "Comparable data" is what
    # BO actually offers per account type; closed accounts routinely have NO
    # wallets and a bare overview — absence of data must not be treated as a
    # mismatch when the identity came from a strong KEY (definitive ticket
    # UUID, operator selection, wallet-IBAN ownership, register number, IBAN
    # search hit). An exact-NAME identity is accepted on missing data only for
    # non-OPEN accounts (an open account should have data; its absence plus a
    # doppelgänger-prone name match goes to the operator instead).
    outcome = MatchOutcome.NO_MATCH.value
    matched_by = "none"
    ticket_provided_iban = (parsed.get("seized_iban") or "").strip()
    ticket_dob = (parsed.get("debtor_dob") or "").strip()
    norm_ibans = {_norm_iban(i) for i in ibans}
    key_identity = ident.get("identified_by") in (
        "manual", "ticket_uuid", "wallet_iban", "register_number", "iban")
    strong_identity = key_identity or bool(ident.get("name_exact"))

    addr = compare_addresses(parsed.get("debtor_address", ""), overview.get("address"))
    reasons.append(f"address check: {addr['grade']} — {addr['detail']} "
                   f"(ticket: {addr['ticket'] or '—'} | account: {addr['account'] or '—'})")

    # Name agreement is a REQUIRED component of every definitive match per the
    # analyst identification matrix (2026-08): Company = name + (address|IBAN),
    # Freelancer = name + (address|DOB|IBAN). The CDD registered/trade name
    # counts too, and slight differences are tolerated — but conflicting
    # explicit legal forms are different legal entities.
    account_names = [business_name, _cdd_registered_name(cdd)]
    if account_type == "Freelancer":
        # A sole trader's businessName is often the TRADE name while the
        # seizure names the PERSON — the owner's PersonFullName counts
        # (the matrix's "Freelancer name"). Deliberately NOT for Companies:
        # a director's name is not the company (S5 territory).
        account_names += _cdd_person_names(cdd)
    if ident.get("name_exact"):
        name_ok, name_note = True, "identified by full-name equality"
    else:
        name_ok, name_note = _name_agreement(parsed.get("debtor_name"), account_names)
    reasons.append(
        "name check: "
        + ("agrees" if name_ok else "not comparable" if name_ok is None else "DIFFERS")
        + f" — {name_note}")

    if account_type == "Company":
        comparable = bool(norm_ibans) or addr["grade"] != UNKNOWN
    else:  # Freelancer or unknown type
        comparable = bool(norm_ibans) or addr["grade"] != UNKNOWN or bool(dob)

    dob_match = _same_dob(ticket_dob, dob)
    iban_hit = bool(ticket_provided_iban) and _norm_iban(ticket_provided_iban) in norm_ibans

    if iban_hit and name_ok:
        outcome, matched_by = MatchOutcome.MATCH.value, "iban"
        reasons.append("IBAN + name match (overrides address)")
    elif addr["grade"] == STRONG and name_ok:
        outcome, matched_by = MatchOutcome.MATCH.value, "address"
        reasons.append(f"{account_type or 'unknown type'}: street-level address match")
    elif addr["grade"] == WEAK and key_identity and name_ok is not False:
        outcome, matched_by = MatchOutcome.MATCH.value, "address"
        reasons.append(
            f"{account_type or 'unknown type'}: weak address evidence accepted — "
            f"identity from strong key {ident.get('identified_by')!r}")
    elif account_type == "Freelancer" and dob_match and name_ok:
        # Spec: a Freelancer is NON-MATCH only when address AND DOB both fail —
        # DOB confirms even across an address mismatch (people move).
        outcome, matched_by = MatchOutcome.MATCH.value, "dob"
        reasons.append("Freelancer: DOB match"
                       + (" (address differs — person may have moved)"
                          if addr["grade"] == MISMATCH else ""))
    elif account_type not in ("Company", "Freelancer") and dob_match and name_ok:
        outcome, matched_by = MatchOutcome.MATCH.value, "dob"
        reasons.append(f"unknown account type {account_type!r}: DOB match")
    elif iban_hit or addr["grade"] == STRONG or dob_match:
        # A positive signal exists but the NAME gate blocked it: per the
        # identification rules that is not definitive (e.g. the ticket's IBAN
        # is on this account but the debtor name names someone else).
        reasons.append(
            "identification signal present (IBAN/address/DOB) but the debtor "
            "name could not be positively matched to the account — not "
            "definitive per the identification rules; operator review")
    elif addr["grade"] == WEAK:
        # Postcode agrees but the street is inconclusive and the identity is
        # name-only: the operator decides (never guess a doppelgänger).
        reasons.append("weak address evidence with a name-only identity — operator must confirm")
        current = {"id": company_uuid, "businessName": business_name,
                   "regNumber": (item.get("regNumber", "") if isinstance(item, dict) else "")}
        return _base(candidates=[current], needs_selection=True,
                     error=("the identified company's address only partially matches the "
                            "ticket (postcode agrees, street inconclusive) — confirm it is "
                            "the debtor or enter the right company UUID"),
                     reasons=reasons, seized_iban=seized_iban, address_check=addr)
    else:
        reasons.append(f"{account_type or 'unknown type'}: address {addr['grade']} -> NO_MATCH")

    if outcome == MatchOutcome.NO_MATCH.value and strong_identity and not comparable:
        if name_ok is False:
            reasons.append(
                "identity key is strong and BO offers no IBAN/address/DOB to "
                "compare — but the debtor name DISAGREES with the account, so "
                "the identity is not auto-accepted")
        elif key_identity or bucket != AccountStatusBucket.OPEN.value:
            # Nothing to compare against (typical for a CLOSED account) and the
            # identity is strong -> accept it, with a visible reason.
            outcome = MatchOutcome.MATCH.value
            matched_by = ident.get("identified_by") or "strong_identity"
            reasons.append(
                "no comparable IBAN/address/DOB data in BO (closed account?) — "
                f"identity accepted from {ident.get('identified_by')!r}")
        else:
            # Exact-name hit on an OPEN account with no data to compare: an
            # open account should have data — the operator confirms instead.
            reasons.append("exact-name identity on an OPEN account without comparable data — operator must confirm")
            current = {"id": company_uuid, "businessName": business_name,
                       "regNumber": (item.get("regNumber", "") if isinstance(item, dict) else "")}
            return _base(candidates=[current], needs_selection=True,
                         error=("found a company with exactly the ticket's name, but the "
                                "account carries no address/IBAN/DOB to verify against — "
                                "confirm it is the debtor"),
                         reasons=reasons, seized_iban=seized_iban, address_check=addr)

    # --- person-vs-company override (Scenario 5) -------------------------------
    # A request against a physical person whose resolved account is a Company.
    # The person's PRIVATE address naturally differs from the company's business
    # address, so this must also fire on a strong identity WITHOUT a confirmed
    # match (live case FPOPCL-23266). Per spec Q10b, first check whether the
    # person has their own Freelancer account — if one exists, the operator
    # picks the right target instead of bigi guessing.
    if (is_physical_person(parsed) and account_type == "Company"
            and (outcome == MatchOutcome.MATCH.value or strong_identity)):
        freelancers = _find_freelancers_by_name(client, parsed.get("debtor_name") or "")
        freelancers = [f for f in freelancers if f.get("id") != company_uuid]
        if freelancers:
            reasons.append(
                "request targets a physical person; a Freelancer account exists "
                "for that name — operator must pick the target")
            current = {"id": company_uuid, "businessName": business_name,
                       "regNumber": (item.get("regNumber", "") if isinstance(item, dict) else "")}
            return _base(
                candidates=[_candidate(f) for f in freelancers] + [current],
                needs_selection=True,
                error=("the request targets a physical person and a Freelancer "
                       "account exists under that name — pick the account the "
                       "seizure is directed at"),
                reasons=reasons, seized_iban=seized_iban)
        outcome = MatchOutcome.PERSON_VS_COMPANY.value
        reasons.append(
            "request is a physical person but the account is a Company "
            "(no Freelancer account found) -> PERSON_VS_COMPANY")

    return _base(
        company_uuid=company_uuid, business_name=business_name,
        matched_by=matched_by, identified_by=ident.get("identified_by"),
        outcome=outcome, account_type=account_type, account_status=account_status,
        status_bucket=bucket, account_status_updated=account_status_updated,
        account_address=account_address, dob=dob, ibans=ibans, reasons=reasons,
        wallets_items=wallets_items, wallets_error=wallets_error, seized_iban=seized_iban,
        seized_iban_source=seized_iban_source, main_wallet=main_wallet,
        address_check=addr,
    )


def _pick_main_wallet(wallets_items: list[dict]):
    """The account's Main wallet, per the spec's derivation order: a wallet
    literally named 'Main' -> a German (DE…) EUR wallet -> any DE IBAN -> any
    EUR wallet -> any wallet with an IBAN. (Live case: an account whose first
    EUR wallet carried a GB IBAN — a §840 letter should quote the DE IBAN.)"""
    items = [w for w in (wallets_items or []) if w.get("iban")]

    def _is(w, *, de=None, eur=None):
        iban_de = str(w.get("iban", "")).strip().upper().startswith("DE")
        cur_eur = str(w.get("currency") or "").strip().upper() == "EUR"
        return (de is None or iban_de == de) and (eur is None or cur_eur == eur)

    for w in items:
        if str(w.get("name", "")).strip().lower() == "main":
            return w
    for w in items:
        if _is(w, de=True, eur=True):
            return w
    for w in items:
        if _is(w, de=True):
            return w
    for w in items:
        if _is(w, eur=True):
            return w
    return items[0] if items else None
