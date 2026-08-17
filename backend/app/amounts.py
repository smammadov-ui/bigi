"""The declared/seizable amount — mini's authoritative rule, per scenario.

For S1/S2 the ``[Seized amount]`` in the §840 declaration is what BO has
actually captured for THIS case (``seizedAmount`` on the ticket's own seizure,
found in ``ignored_same_case``) — the wallet balance reads ~0 once funds move
under the seizure, and prior seizures drain first. Fallback when the own-case
seizure is missing (surfaced as a warning): ``min(claim, available)``.

The full app hardcoded S2 -> 0,00 (an assumption) and used a pre-create
balance snapshot for S1 — both replaced by this rule (the engine's merge).

For S6B (T11: "wir können Ihnen nur den Restbetrag überweisen") the figure IS
the remaining transferable balance: ``min(claim, available)`` — the transfer
itself is handled in BO, never by bigi.
"""
from __future__ import annotations

from .formatting import iso_date_any, parse_decimal
from .schemas import Scenario


def compute_seized_amount(scenario: str, parsed: dict, balance: dict, seizure_check: dict) -> dict:
    """Return ``{seized_eur, source, warnings}``. ``seized_eur`` may be None
    (unknown — the template renders its default and the pipeline warns)."""
    warnings: list[str] = []
    claim = parse_decimal(parsed.get("seizure_amount"))
    available = balance.get("available_eur")

    if scenario in (Scenario.S1.value, Scenario.S2.value):
        own_rows = seizure_check.get("ignored_same_case") or []
        if own_rows:
            latest = max(own_rows, key=lambda s: iso_date_any(s.get("created") or ""))
            if latest.get("seized_amount") is not None:
                return {"seized_eur": float(latest["seized_amount"]),
                        "source": "bo_own_case_seized_amount", "warnings": warnings}
            warnings.append("own-case seizure found but carries no seizedAmount — falling back to min(claim, available)")
        else:
            warnings.append("own-case seizure not found in BO — was this ticket's seizure submitted? Falling back to min(claim, available)")
        return {**_min_claim_available(claim, available), "warnings": warnings}

    if scenario == Scenario.S6B.value:
        # Remaining transferable balance for T11. Funds already captured by
        # settling seizures (PendingTransferApproval) still read on the wallets
        # but are spoken for — only the rest is transferable (FPOPCL-31278).
        settling = seizure_check.get("settling") or []
        captured = round(sum(float(s.get("seized_amount") or 0) for s in settling), 2)
        if available is not None and captured > 0:
            available = round(max(float(available) - captured, 0.0), 2)
            warnings.append(
                f"available balance reduced by {captured:.2f} EUR already captured "
                "by seizure(s) pending transfer approval")
        return {**_min_claim_available(claim, available), "warnings": warnings}

    # All other scenarios declare no seized amount (T6 renders 0,00 by default).
    return {"seized_eur": 0.0, "source": "not_applicable", "warnings": warnings}


def _min_claim_available(claim, available) -> dict:
    if available is None:
        if claim is not None:
            # Balance unknown -> last resort is the claim itself (mini's rule);
            # the pipeline surfaces a review warning alongside.
            return {"seized_eur": round(float(claim), 2), "source": "claim_fallback_unknown_balance"}
        return {"seized_eur": None, "source": "unknown"}
    if claim is not None:
        return {"seized_eur": round(min(float(claim), float(available)), 2),
                "source": "min_claim_available"}
    return {"seized_eur": round(float(available), 2), "source": "available_balance"}
