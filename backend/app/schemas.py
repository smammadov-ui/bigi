"""Pydantic request models, the shared error hierarchy, and the closed
scenario vocabulary every module agrees on.

The vocabulary (Scenario / MatchOutcome / AccountStatusBucket) is ported from
the feizure engine so bigi resolves the same 11 coded scenarios as the full
app. bigi is READ-ONLY toward Back-Office: no scenario creates a seizure here
— by the time a TPD is generated, this ticket's seizure is expected to already
exist in BO (surfaced as the "own case" in the seizure check).

The pipeline result returned by ``/api/declaration`` / ``/api/webhook/jira`` /
``/api/jira/fetch`` is a plain dict (documented in ``pipeline.py``) — no strict
response model is needed for it. The errors below are mapped to HTTP status
codes by the routers (``BigiError.code`` -> ``HTTPException``).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


# --- request models ---------------------------------------------------------
class DeclarationRequest(BaseModel):
    raw_text: str
    # operator override after candidate selection / manual entry
    company_uuid: str | None = None
    # operator declared "none of the candidates is the debtor" -> force
    # NO_MATCH (Scenario 4: T7 without a ticket IBAN, T8 with one)
    no_match: bool = False
    # manual mode: operator-corrected parsed fields (key -> new value); applied
    # after parsing, before identification — the edit re-runs the checks
    field_overrides: dict | None = None


class ComposeRequest(BaseModel):
    """Manual-mode recompose: the (edited) decision set + the context echoed
    from the run that produced it. Stateless — no BO call, no pipeline re-run."""

    decisions: dict
    context: dict = {}
    auto: dict | None = None


class JiraFetchRequest(BaseModel):
    issue_key: str


class SettingsPatch(BaseModel):
    llm: dict | None = None              # {provider, model, api_key}
    bo: dict | None = None               # {base_url, inttoken}
    jira: dict | None = None             # {base_url, email, api_token, jql}


# --- error hierarchy --------------------------------------------------------
class BigiError(Exception):
    """Base service error. ``code`` is the HTTP status the router maps it to."""

    code = 400


class BigiNotFound(BigiError):
    code = 404


class BigiUpstream(BigiError):
    code = 502


# ---------------------------------------------------------------------------
# Step 1/2 — match outcome + account-status bucket
# ---------------------------------------------------------------------------
class MatchOutcome(str, Enum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    PERSON_VS_COMPANY = "PERSON_VS_COMPANY"


class AccountStatusBucket(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CLOSING = "CLOSING"
    RESTRICTED = "RESTRICTED"
    ONBOARDING = "ONBOARDING"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Step 5 — the 11 coded scenarios (the single closed label set)
# ---------------------------------------------------------------------------
class Scenario(str, Enum):
    S1 = "S1"                      # normal TPD -> T1
    S2 = "S2"                      # prior Processing seizures -> T2
    S3 = "S3"                      # closed / onboarding -> T6
    S4_NO_IBAN = "S4_NO_IBAN"      # no match, no IBAN -> T7
    S4_IBAN = "S4_IBAN"            # no match, IBAN given -> T8
    S5 = "S5"                      # person vs company -> T9
    S6A = "S6A"                    # closing, covered -> T10
    S6B = "S6B"                    # closing, has balance -> T11
    INSOLVENCY = "INSOLVENCY"      # MNL21 -> T4
    RFI = "RFI"                    # MNL22 -> T5
    ROUTED_OUT = "ROUTED_OUT"      # criminal / restricted / manual review


# Scenarios whose §840 declaration presumes this ticket's seizure was already
# submitted in BO ("own case"). bigi never creates it — a missing own-case
# seizure is surfaced as a warning, not an action.
OWN_CASE_SCENARIOS = frozenset({Scenario.S1, Scenario.S2})

SCENARIOS: tuple[str, ...] = tuple(s.value for s in Scenario)


def is_scenario(label) -> bool:
    """True iff ``label`` is exactly one of the coded Scenario values."""
    return isinstance(label, str) and label in set(SCENARIOS)
