"""Deterministic Jira -> parsed-fields parser (self-contained, no LLM).

Adapted from the parent app's ``backend/app/services/parser.py`` but with no
dependency on the parent ``schemas`` module: ``PARSED_FIELDS`` and
``empty_parsed`` are defined locally (the subset bigi needs), and only
``parse_date_iso`` / ``parse_decimal`` are imported from ``.formatting``.

Rules (see ``spec/parse_spec.md``):

* Field lines are ``key: value`` with an OPTIONAL leading ``*`` / ``-`` / ``•``
  bullet (real pastes often lose their bullets). Keys are lowercased + trimmed
  and matched against an allowlist; branch on a **non-empty value** (a present
  but empty ``seized IBANs:`` counts as absent).
* Strip a trailing ``Additional information`` / ``Secondary information`` noise
  suffix from values; skip a line that is *only* that phrase.
* A non-empty line with no recognized ``key:`` that follows the first field line
  is a multi-line continuation of the previous field's value (whitespace
  collapsed). An UNrecognized ``key:`` line resets the continuation anchor.
* ``seizure_amount`` is normalised via ``parse_decimal`` to ``"{:.2f}"`` (never
  ``0``); a non-empty-but-unparseable amount halts.
* ``date_received`` / ``issued_date`` are normalised to ISO via ``parse_date_iso``.
* ``company_uuid``: UUID-validated tokens from ``definitive match`` +
  ``potential match``. Exactly one distinct -> ``company_uuid``; several ->
  ``company_uuid_candidates`` (wallet-IBAN disambiguation / selection at
  checks); none -> NOT a halt; warning — resolved later by search.
  ``seized_iban`` falls back to a single ``debtor list of IBANs`` entry.
* A line starting the appended ``Third Party Declaration`` template block ends
  field parsing — the block is not ticket data.
* ``seized_iban`` is validated and halts (never guesses) on a non-empty invalid
  value; multiple debtor IBANs halt; an invalid ``creditor_bic`` is a warning.
* ``creditor_name`` / ``issued_date`` are also extracted from prose
  ("request from <name> issued on <date>").
"""
from __future__ import annotations

import re

from .formatting import parse_date_iso, parse_decimal

# ---------------------------------------------------------------------------
# Parsed-field set (the subset bigi needs). Absent -> "".
# ---------------------------------------------------------------------------
PARSED_FIELDS: tuple[str, ...] = (
    "company_uuid", "company_uuid_candidates", "seized_iban", "debtor_ibans",
    "seizure_type", "seizure_amount",
    "document_type", "date_received", "debtor_name", "debtor_address", "case_references",
    "creditor_bank", "creditor_iban", "creditor_bic", "creditor_address", "creditor_email",
    "creditor_name", "comment", "porters_document_id", "issued_date",
    "debtor_dob", "debtor_tax_id", "debtor_register_number", "debtor_legal_rep",
)


def empty_parsed() -> dict[str, str]:
    return {k: "" for k in PARSED_FIELDS}


# Lowercased bullet key -> canonical parsed-field name. Keys not in this map
# (``definitive match`` / ``potential match`` -> company_uuid, ``seized ibans``
# -> seized_iban) are handled separately below.
_KEY_MAP: dict[str, str] = {
    "debtor list of ibans": "debtor_ibans",
    "seizure type": "seizure_type",
    "seizure amount": "seizure_amount",
    "document type": "document_type",
    "date received": "date_received",
    "debtor name": "debtor_name",
    "debtor address": "debtor_address",
    "case references": "case_references",
    "creditor bank": "creditor_bank",
    "creditor iban": "creditor_iban",
    "creditor bic": "creditor_bic",
    "creditor address": "creditor_address",
    "creditor email": "creditor_email",
    "porters document id/ new file name": "porters_document_id",
    "debtor date of birth": "debtor_dob",
    "debtor tax id": "debtor_tax_id",
    "debtor register number": "debtor_register_number",
    "debtor lr": "debtor_legal_rep",
    # RFI tickets use the subject/requester vocabulary (live FPOPCL-24605) —
    # aliased onto the debtor/creditor fields so identification + confirmation
    # work unchanged for information requests.
    "subject name": "debtor_name",
    "subject lr": "debtor_legal_rep",
    "subject ibans": "debtor_ibans",
    "subject date of birth": "debtor_dob",
    "subject tax id": "debtor_tax_id",
    "subject register number": "debtor_register_number",
    "subject address": "debtor_address",
    "rfi type": "seizure_type",
    "requester name": "creditor_name",
    "requester email": "creditor_email",
    "requester address": "creditor_address",
}

# Keys that carry their own value (not in _KEY_MAP) but are still real field
# lines for the purposes of continuation detection / comment cut-off.
_SPECIAL_KEYS = frozenset({"seized ibans", "definitive match", "potential match",
                           "definitive matches", "potential matches"})

# A ``key: value`` field line. The leading bullet marker (``*``/``-``/``•``) is
# OPTIONAL. The key allowlist gates whether a colon line is treated as a field;
# prose lines that merely contain a colon are not.
_BULLET_RE = re.compile(r"^\s*[*\-•]?\s*([^:]+?)\s*:\s*(.*)$")

# Trailing section-title noise to strip from values / comment.
_NOISE_RE = re.compile(
    r"\s*(?:additional information|secondary information)\s*$", re.IGNORECASE
)
# A line that is *only* the section-title phrase (ignore entirely).
_NOISE_ONLY_RE = re.compile(
    r"^\s*(?:additional information|secondary information)\s*$", re.IGNORECASE
)

# Prose extraction. Accept several "request from <name> issued on <date>" phrasings.
_CREDITOR_NAME_RE = re.compile(
    r"(?:seizure\s+request|request)\s+from\s+(.+?)\s+issued\s+on", re.IGNORECASE
)
# Old tickets carry the amount only in prose: "The amount of the seizure is 6487.21."
_PROSE_AMOUNT_RE = re.compile(
    r"amount\s+of\s+the\s+seizure\s+is\s+([\d.,]+)", re.IGNORECASE
)
# Porters sometimes serializes case references as a Python-repr list of dicts:
# "[{'reference': '2814/... F'}, {'reference': 'SG 11/30'}]" -> "2814/... F, SG 11/30"
_REFERENCE_ITEM_RE = re.compile(r"'reference'\s*:\s*'([^']*)'")
# ``issued on <date>`` where the date is ISO / DD.MM.YYYY / DD/MM/YYYY.
_ISSUED_DATE_RE = re.compile(
    r"issued\s+on\s+(\d{1,4}[-./]\d{1,2}[-./]\d{1,4})", re.IGNORECASE
)

# Validation.
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_BIC_RE = re.compile(r"^[A-Z0-9]{8}([A-Z0-9]{3})?$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# Porters tickets append a pre-rendered TPD template after the field lines.
# That block is NOT ticket data: its first line ("Third Party Declaration
# (Drittschuldnererklärung)") has no colon and would otherwise be glued onto
# the previous field as a continuation (a trailing empty "seized IBANs:" then
# fails IBAN validation and falsely halts). Everything from this line on is
# ignored.
_TPD_BLOCK_RE = re.compile(r"^\s*third party declaration\b", re.IGNORECASE)

# Split a debtor-IBAN list on comma / semicolon / whitespace.
_IBAN_SPLIT_RE = re.compile(r"[,;\s]+")

# Collapse internal whitespace runs into single spaces.
_WS_RE = re.compile(r"\s+")

# Normalise a register number: drop whitespace, upper-case. "HRB 990011" -> "HRB990011".
_REG_WS_RE = re.compile(r"\s+")




def _strip_noise(value: str) -> str:
    """Remove a trailing 'Additional/Secondary information' suffix + whitespace."""
    return _NOISE_RE.sub("", value).strip()


def _field_key(line: str):
    """Return ``(key, raw_value)`` for an allowlisted field line, else ``None``.

    A line is a *field* line only if it matches ``key: value`` AND the lowercased
    key is in the allowlist (mapped fields or the special company/seized keys).
    Other colon-bearing lines (prose) return ``None`` and are not fields.
    """
    m = _BULLET_RE.match(line)
    if not m:
        return None
    key = m.group(1).strip().lower()
    if key in _KEY_MAP or key in _SPECIAL_KEYS:
        return key, m.group(2)
    return None


def parse_jira(raw_text: str) -> dict:
    """Parse a Jira seizure request into the canonical parsed-fields object.

    Returns a dict with:
      - ``fields``: every ``PARSED_FIELDS`` key present; absent -> ``""``.
      - ``halted``: True when a required field is missing/invalid/unparseable.
      - ``halt_reasons``: human-readable reasons for the halt.
      - ``warnings``: non-fatal issues (e.g. an implausible BIC).
      - ``seized_iban_source``: "provided" | "debtor_list" | "".
    """
    raw_text = raw_text or ""
    fields = empty_parsed()
    halt_reasons: list[str] = []
    warnings: list[str] = []
    halted = False

    lines = raw_text.splitlines()

    # Index of the first field line; prose before it is the comment, and only
    # lines after it can be continuations.
    first_field_idx = None
    for i, line in enumerate(lines):
        if _field_key(line) is not None:
            first_field_idx = i
            break

    # --- Step 1: field lines + multi-line continuations ----------------------
    # Raw values keyed by lowercased key (special keys resolved separately).
    bullets: dict[str, str] = {}
    last_key = None  # the key whose value a continuation line extends

    if first_field_idx is not None:
        for line in lines[first_field_idx:]:
            if _TPD_BLOCK_RE.match(line):
                # Start of the appended TPD template block: stop field parsing.
                break
            if _NOISE_ONLY_RE.match(line):
                # A line that is only the section-title phrase: ignore entirely
                # (neither a field nor a continuation).
                continue
            fk = _field_key(line)
            if fk is not None:
                key, raw_value = fk
                bullets[key] = _strip_noise(raw_value.strip())
                last_key = key
            elif _BULLET_RE.match(line) is not None:
                # A `key: value` line whose key is NOT recognized (e.g.
                # "original file name: …"). Ignore it — it is NOT a continuation
                # of the previous field. Reset the anchor so a later genuine
                # continuation can't jump back over it to an earlier field.
                last_key = None
            elif line.strip() and last_key is not None:
                # Genuine continuation: a non-empty line with no `key:` structure.
                extra = _strip_noise(line.strip())
                if extra:
                    joined = f"{bullets.get(last_key, '')} {extra}".strip()
                    bullets[last_key] = _WS_RE.sub(" ", joined)
            # else: blank line / pre-field state -> ignored.

    # Assign mapped fields, branching on a non-empty value.
    for key, value in bullets.items():
        mapped = _KEY_MAP.get(key)
        if mapped is not None and value:
            fields[mapped] = value

    # --- Step 2: prose extraction --------------------------------------------
    name_m = _CREDITOR_NAME_RE.search(raw_text)
    if name_m:
        fields["creditor_name"] = name_m.group(1).strip()

    date_m = _ISSUED_DATE_RE.search(raw_text)
    if date_m:
        fields["issued_date"] = parse_date_iso(date_m.group(1))

    # Comment: the leading prose before the first field line (noise stripped).
    if first_field_idx is None:
        prose_lines = lines
    else:
        prose_lines = lines[:first_field_idx]
    comment = _strip_noise("\n".join(prose_lines).strip())
    fields["comment"] = comment

    # Case references may arrive as a repr'd list of dicts — flatten to the
    # plain reference strings (comma-joined).
    refs = _REFERENCE_ITEM_RE.findall(fields["case_references"])
    if refs:
        fields["case_references"] = ", ".join(r.strip() for r in refs if r.strip())

    # Old tickets carry the amount only in prose — fall back to it.
    if not fields["seizure_amount"]:
        m = _PROSE_AMOUNT_RE.search(raw_text)
        if m:
            fields["seizure_amount"] = m.group(1).rstrip(".")

    # --- Step 3: seizure_amount (normalise; never store 0) -------------------
    raw_amount = fields["seizure_amount"]
    if raw_amount:
        num = parse_decimal(raw_amount)
        if num is None:
            halted = True
            halt_reasons.append(f"seizure_amount unparseable: {raw_amount!r}")
            fields["seizure_amount"] = ""
        else:
            fields["seizure_amount"] = f"{num:.2f}"

    # --- Step 4: dates (normalise to ISO) ------------------------------------
    if fields["date_received"]:
        fields["date_received"] = parse_date_iso(fields["date_received"])

    # --- Step 5: company_uuid (RESOLVED later — not required at parse) -------
    # definitive match / potential match may each carry zero, one, or several
    # UUIDs (real tickets list two comma-separated candidates). Every token is
    # validated against the UUID format — a malformed value must never reach BO
    # (it 400s every endpoint). Exactly one distinct valid UUID -> company_uuid.
    # Several -> company_uuid_candidates (disambiguated at the checks step by
    # comparing each candidate's BO wallet IBANs against the ticket's seized/
    # debtor IBAN, else operator selection). None is NOT a halt — the account
    # is identified by searching on register number / seized IBAN / name.
    uuids: list[str] = []
    for raw in (bullets.get("definitive match", ""), bullets.get("definitive matches", ""),
                bullets.get("potential match", ""), bullets.get("potential matches", "")):
        for token in _IBAN_SPLIT_RE.split(raw.strip()):
            if not token:
                continue
            if _UUID_RE.match(token):
                if token.lower() not in (u.lower() for u in uuids):
                    uuids.append(token)
            else:
                warnings.append(f"ignored malformed company UUID: {token!r}")
    if len(uuids) == 1:
        fields["company_uuid"] = uuids[0]
    elif uuids:
        fields["company_uuid_candidates"] = ", ".join(uuids)
        warnings.append(
            "multiple company UUIDs on ticket (definitive/potential match); "
            "will disambiguate by wallet IBAN at checks"
        )
    else:
        warnings.append(
            "no company_uuid (definitive/potential match empty); will identify by search at checks"
        )

    # --- Step 6: seized_iban (RESOLVED later — not required at parse) --------
    # provided seized IBAN -> single debtor IBAN -> (else resolved at the checks
    # step from the matched account's Main wallet). An empty value means absent;
    # a NON-empty masked/invalid IBAN still halts (never guess).
    seized_raw = bullets.get("seized ibans", "").strip()
    seized_iban_source = ""
    if seized_raw:
        candidate = seized_raw
        candidate_source = "provided"
    else:
        debtor_raw = fields["debtor_ibans"].strip()
        parts = [p for p in _IBAN_SPLIT_RE.split(debtor_raw) if p]
        if len(parts) > 1:
            halted = True
            halt_reasons.append(
                "multiple debtor IBANs; human must select the seized account"
            )
            candidate = ""
        else:
            candidate = parts[0] if parts else ""
        candidate_source = "debtor_list"

    if candidate and _IBAN_RE.match(candidate):
        fields["seized_iban"] = candidate
        seized_iban_source = candidate_source
    elif "multiple debtor IBANs; human must select the seized account" in halt_reasons:
        pass  # already halted on multiplicity
    elif not candidate:
        # absent: NOT a halt — resolve at checks from the matched account's Main wallet.
        warnings.append(
            "seized_iban not provided; will derive from the matched account's Main wallet at checks"
        )
    else:
        halted = True
        halt_reasons.append(
            f"seized_iban invalid (masked/partial or fails IBAN format): {candidate!r}"
        )

    # --- Step 7: creditor_bic validation (warn, do not halt) -----------------
    bic = fields["creditor_bic"]
    if bic and not _BIC_RE.match(bic):
        warnings.append(f"creditor_bic does not match BIC format: {bic!r}")
        # Value retained as the stripped raw string.

    # Defensive: ensure exactly the canonical key set is present.
    for key in PARSED_FIELDS:
        fields.setdefault(key, "")

    return {
        "fields": fields,
        "halted": halted,
        "halt_reasons": halt_reasons,
        "warnings": warnings,
        "seized_iban_source": seized_iban_source,  # "provided" | "debtor_list" | ""
    }
