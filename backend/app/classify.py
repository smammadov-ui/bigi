"""Step 0 — ticket-level classification (before any BO call).

Live-verified against Porters' own vocabulary:

* RFI (FPOPCL-24605): "We received an RFI of type public_prosecutor_investigation
  from … regarding …" + a ``rfi type:`` field line. An RFI is NOT a seizure —
  the authority wants data (T5 guidance), never a §840 declaration.
* Criminal seizure (FPOPCL-24619): ``seizure type: seizure_warrant`` +
  Staatsanwaltschaft as the requesting authority + a criminal ``Js`` docket
  number. Criminal cases are handled CONFIDENTIALLY via the MNL20 alert
  (manual + four-eyes): the standard flow would tip the customer off.
* Everything else (``public_creditor_seizure`` …) -> the civil pipeline.

Deterministic and conservative: only explicit markers classify a ticket away
from the civil flow.
"""
from __future__ import annotations

import re

CIVIL = "civil_seizure"
RFI_KIND = "rfi"
CRIMINAL = "criminal_seizure"

_RFI_PROSE_RE = re.compile(r"\breceived\s+an\s+RFI\b", re.IGNORECASE)
_RFI_FIELD_RE = re.compile(r"(?m)^\s*[*\-•]?\s*rfi\s+type\s*:", re.IGNORECASE)
_WARRANT_RE = re.compile(r"seizure[\s_]+warrant", re.IGNORECASE)
_PROSECUTOR_RE = re.compile(
    r"staatsanwaltschaft|generalstaatsanwalt|landeskriminalamt|polizeipräsidium|kriminalpolizei",
    re.IGNORECASE,
)
# "4111 Js 138236" / "123 Js 1092/26" — the Js file number of a German
# criminal prosecution docket.
_JS_DOCKET_RE = re.compile(r"\b\d+\s*Js\s*\d+", re.IGNORECASE)


def classify_ticket(raw_text: str, fields: dict) -> tuple[str, list[str]]:
    """Return ``(kind, reasons)`` with kind in {civil_seizure, rfi, criminal_seizure}."""
    text = raw_text or ""
    fields = fields or {}

    if _RFI_PROSE_RE.search(text) or _RFI_FIELD_RE.search(text):
        return RFI_KIND, [
            "ticket is an information request (RFI) — gather the requested data; "
            "no seizure, no §840 declaration"
        ]

    reasons: list[str] = []
    seizure_type = str(fields.get("seizure_type") or "")
    if "warrant" in seizure_type.lower() or _WARRANT_RE.search(text):
        reasons.append(f"criminal seizure warrant (seizure type {seizure_type or 'from prose'!r})")
    if _PROSECUTOR_RE.search(str(fields.get("creditor_name") or "")):
        reasons.append("requesting authority is a public prosecutor / criminal police")
    if _JS_DOCKET_RE.search(str(fields.get("case_references") or "")):
        reasons.append("criminal Js docket number in the case references")
    if reasons:
        return CRIMINAL, reasons

    return CIVIL, []
