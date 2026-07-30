"""Cross-workspace search support (SOP Step 1: check FP *and* PNL).

The BO workspace (``FinomPayments`` / ``PnlFintech``) is server-side user
state: searches only see companies in the ACTIVE contexts. Operators are told
to check both sides; bigi does the same by widening the user's active contexts
to ALL available ones for the duration of a single case, then restoring the
original selection — a session preference, not a business-data write.

Degrades gracefully: any failure (whoami/gated/set rejected) leaves the
current context untouched and the pipeline continues single-workspace with a
visible warning.
"""
from __future__ import annotations

from contextlib import contextmanager

from .bo_client import BOError


@contextmanager
def all_workspaces(client):
    """Context manager: widen active workspaces to all available, restore after.

    Yields an info dict: ``{"available", "original", "switched", "error"}`` —
    the caller turns it into warnings/reasons. Restoration runs in ``finally``
    even when the pipeline raises; a failed restore is surfaced via
    ``info["restore_error"]`` (checked after the with-block).
    """
    info = {"available": [], "original": [], "switched": False,
            "error": None, "restore_error": None}
    try:
        profile = client.whoami() or {}
        available = [str(c) for c in (profile.get("contexts") or []) if c]
        active = [str(c) for c in (profile.get("activeContexts") or []) if c]
        info["available"], info["original"] = available, active
    except BOError as exc:
        info["error"] = f"whoami failed ({exc.status_code or 'transport'}) — single-workspace search"
        yield info
        return
    except AttributeError:
        # Duck-typed client without workspace support -> single-workspace mode.
        yield info
        return

    if len(info["available"]) <= 1 or set(info["available"]) <= set(info["original"]):
        # Nothing to widen (single workspace, or everything already active).
        yield info
        return

    try:
        client.set_user_contexts(info["available"])
        info["switched"] = True
    except BOError as exc:
        info["error"] = (f"could not widen workspaces ({exc.status_code or 'transport'}) "
                         "— single-workspace search")
        yield info
        return

    try:
        yield info
    finally:
        try:
            client.set_user_contexts(info["original"])
        except BOError as exc:
            info["restore_error"] = (
                f"failed to restore the original workspace selection "
                f"({', '.join(info['original'])}) — reset it via the BO portal "
                f"(error: {exc.status_code or 'transport'})")
