"""Pipeline orchestration: parse -> identify+confirm -> alerts -> seizures ->
balance -> scenario -> amount -> document (all 11 scenarios, READ-ONLY).

The single public entry point ``run_pipeline`` NEVER raises for BO/LLM problems
— upstream failures are surfaced in ``account.error`` / ``alerts.error`` /
``seizure_check.error`` and the scenario resolver treats them conservatively
(undecidable cases route to the operator). Only truly invalid input (empty
``raw_text``) raises ``BigiError(400)``.

bigi never writes to Back-Office. For S1/S2 the §840 declaration presumes this
ticket's seizure was already submitted in BO (its Processing row is recognised
as the "own case" and its ``seizedAmount`` is the authoritative declared
figure); a missing own case is a warning, not an action.

Result shape (EXACT keys)::

    {
      "status":  "ok" | "pending_selection" | "halted",
      "parsed":  { ...PARSED_FIELDS..., "warnings":[...], "halted":bool, "halt_reasons":[...] },
      "account": { "company_uuid","business_name","matched_by","identified_by",
                   "candidates","needs_selection","error",
                   "outcome","account_type","account_status","status_bucket",
                   "account_status_updated","account_address","dob","ibans",
                   "reasons","seized_iban","seized_iban_source","main_wallet" } | null,
      "alerts":  { "open_rules","open_count","total","error","assumed" } | null,
      "balance": { "available_eur","available_eur_de","breakdown","non_eur","error",
                   "seizable_eur","seizable_eur_de","held_eur","held_eur_de",
                   "client_total_eur","client_total_eur_de" } | null,
      "seizure_check": { "processing_count","seizures","ignored_same_case",
                         "ignored_later","own_case_missing","error","assumed" } | null,
      "scenario": "S1"…"ROUTED_OUT" | null (pending selection / halted),
      "plan":     { "scenario","template","action","rationale","notes" } | null,
      "amount":   { "seized_eur","source","warnings" } | null,
      "declaration": { "template","kind","text","subject","composed_by" } | null,
      "warnings": [ ... aggregated review warnings ... ]
    }
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import checks as checks_mod
from . import llm
from .amounts import compute_seized_amount
from .bo_client import BOClient, BOError
from .classify import CRIMINAL, REPEAL, RESTRICTION, RFI_KIND, classify_ticket
from .decisions import build_manual
from .formatting import de_amount
from .matching import match_account
from .parser import parse_jira
from .schemas import BigiError, MatchOutcome, Scenario
from .scenarios import build_plan, resolve_scenario
from .settings_store import bo_config, llm_config
from .templates import TEMPLATES, build_subject, select_template, template_kind
from .workspaces import all_workspaces


def _account_public(match: dict) -> dict:
    """The account dict shipped to the frontend (wallets payload stripped)."""
    out = dict(match or {})
    out.pop("wallets_items", None)
    return out


def _alerts_public(alerts: dict) -> dict:
    """Compact alerts view: rule codes + counts, never the raw BO items."""
    items = alerts.get("items") or []
    open_rules = list(alerts.get("open_rules") or [])
    return {
        "open_rules": open_rules,
        "open_count": sum(1 for a in items if a.get("resolvedOn") is None),
        "total": len(items),
        "error": alerts.get("error"),
        "assumed": bool(alerts.get("assumed")),
    }


def run_pipeline(db: Session, raw_text: str, company_uuid: str | None = None,
                 comment_uuids: list[str] | None = None,
                 no_match: bool = False,
                 field_overrides: dict | None = None) -> dict:
    """Run the full declaration pipeline and return the editable result dict.

    ``comment_uuids``: company UUIDs found in the ticket's Jira COMMENTS
    (submitters post definitive/potential matches there when the description
    lacks them). Merged with the description's UUIDs: one distinct value
    resolves directly, several go through the candidates path (wallet
    disambiguation / operator picker).

    ``no_match``: the operator declared that NONE of the offered candidates is
    the debtor — identification is skipped and the case resolves as NO_MATCH
    (Scenario 4 -> T7/T8 email to the creditor).

    ``field_overrides``: manual mode — operator-corrected parsed fields
    (key -> value), applied AFTER parsing and BEFORE identification, so the
    correction re-runs identification + checks. Applied keys are surfaced as a
    parse warning and in ``parsed["edited_fields"]``.
    """
    if not raw_text or not raw_text.strip():
        raise BigiError("raw_text is required")

    warnings: list[str] = []

    # --- Step 1: parse --------------------------------------------------------
    p = parse_jira(raw_text)
    fields = p["fields"]

    if comment_uuids:
        desc_uuids = [u for u in ([fields.get("company_uuid")] if fields.get("company_uuid") else [])]
        desc_uuids += [u.strip() for u in (fields.get("company_uuid_candidates") or "").split(",") if u.strip()]
        merged: list[str] = []
        for u in desc_uuids + [str(c).strip() for c in comment_uuids if str(c).strip()]:
            if u.lower() not in (m.lower() for m in merged):
                merged.append(u)
        if merged != desc_uuids:
            if len(merged) == 1:
                fields["company_uuid"] = merged[0]
                fields["company_uuid_candidates"] = ""
                p["warnings"] = list(p.get("warnings") or []) + [
                    "company UUID taken from a Jira comment (definitive/potential match)"]
            else:
                fields["company_uuid"] = ""
                fields["company_uuid_candidates"] = ", ".join(merged)
                p["warnings"] = list(p.get("warnings") or []) + [
                    f"{len(merged)} company UUIDs from description+comments — "
                    "disambiguating by wallet IBAN / operator selection"]
    edited_fields: list[str] = []
    if field_overrides:
        for k, v in field_overrides.items():
            if k in fields and str(fields.get(k) or "") != str(v or ""):
                fields[k] = str(v or "")
                edited_fields.append(k)
        if edited_fields:
            p["warnings"] = list(p.get("warnings") or []) + [
                "operator-edited fields: " + ", ".join(sorted(edited_fields))]

    parsed = {
        **fields,
        "warnings": p.get("warnings", []),
        "halted": p.get("halted", False),
        "halt_reasons": p.get("halt_reasons", []),
        "edited_fields": edited_fields,
    }

    if parsed["halted"]:
        # Required fields missing/invalid — no responsible decision is possible
        # and no BO call is made. The operator fixes the ticket and re-runs.
        return {
            "status": "halted",
            "parsed": parsed,
            "account": None, "alerts": None, "balance": None, "seizure_check": None,
            "scenario": None, "plan": None, "amount": None, "declaration": None,
            "manual": build_manual(parsed=parsed),
            "warnings": [f"halted: {r}" for r in parsed["halt_reasons"]],
        }

    # --- Step 1b: classify the ticket (criminal / RFI / civil) — Step 0 of the
    # decision algorithm; runs BEFORE any BO call.
    kind, cls_notes = classify_ticket(raw_text, fields)
    if kind == CRIMINAL:
        notes = cls_notes + [
            "criminal cases are handled CONFIDENTIALLY via the MNL20 alert "
            "(manual + four-eyes) — the standard flow would tip the customer off"
        ]
        return {
            "status": "ok",
            "parsed": parsed,
            "account": None, "alerts": None, "balance": None, "seizure_check": None,
            "scenario": Scenario.ROUTED_OUT.value,
            "plan": build_plan(Scenario.ROUTED_OUT.value, notes),
            "amount": None, "declaration": None,
            "manual": build_manual(parsed=parsed, scenario=Scenario.ROUTED_OUT.value),
            "warnings": [f"classified: {n}" for n in notes],
        }

    # --- Step 2: BO client ----------------------------------------------------
    bo = bo_config(db)
    client = BOClient(bo.get("base_url", ""), bo.get("inttoken", ""))

    # --- Step 2b: widen to ALL workspaces (SOP: check FP *and* PNL) -----------
    # Server-side session preference, restored in the finally of the context
    # manager. On failure the pipeline continues single-workspace with a warning.
    with all_workspaces(client) as ws:
        result = _run_checks_and_compose(db, client, raw_text, parsed, fields,
                                         company_uuid, kind, cls_notes, warnings,
                                         no_match=no_match)
    if ws.get("switched"):
        result["warnings"].append(
            "searched across workspaces: " + ", ".join(ws.get("available") or [])
            + " (active selection restored)")
    if ws.get("error"):
        result["warnings"].append(f"workspaces: {ws['error']}")
    if ws.get("restore_error"):
        result["warnings"].append(f"workspaces: {ws['restore_error']}")
    return result


def _identification_failed(parsed: dict, fields: dict, exc) -> dict:
    """Identification itself failed (search down) — nothing to decide on.
    The operator can enter a UUID manually or retry later."""
    account = {
        "company_uuid": "", "business_name": "", "matched_by": "none",
        "identified_by": None, "candidates": [], "needs_selection": True,
        "error": str(exc), "outcome": None, "account_type": "",
        "account_status": "", "status_bucket": "UNKNOWN",
        "account_status_updated": "", "account_address": "", "dob": "",
        "ibans": [], "reasons": [], "seized_iban": fields.get("seized_iban", ""),
        "seized_iban_source": None, "main_wallet": None, "address_check": None,
    }
    return {
        "status": "pending_selection",
        "parsed": parsed,
        "account": account,
        "alerts": None, "balance": None, "seizure_check": None,
        "scenario": None, "plan": None, "amount": None, "declaration": None,
        "manual": build_manual(parsed=parsed, account=account),
        "warnings": [f"identification failed: {exc}"],
    }


def _run_checks_and_compose(db: Session, client, raw_text: str, parsed: dict,
                            fields: dict, company_uuid, kind, cls_notes,
                            warnings: list, no_match: bool = False) -> dict:
    """Steps 3–10 (matching -> checks -> scenario -> document); see run_pipeline."""
    # --- Step 3: identify + confirm + status (one wallets call, reused) -------
    if no_match:
        # Operator's call: none of the candidates is the debtor. No further
        # identification — the case is a Scenario-4 "cannot find the user".
        match = {
            "company_uuid": "", "business_name": "", "matched_by": "none",
            "identified_by": None, "candidates": [], "needs_selection": False,
            "error": None, "outcome": MatchOutcome.NO_MATCH.value,
            "account_type": "", "account_status": "", "status_bucket": "UNKNOWN",
            "account_status_updated": "", "account_address": "", "dob": "",
            "ibans": [], "wallets_items": [], "wallets_error": None,
            "seized_iban": fields.get("seized_iban", ""),
            "seized_iban_source": None, "main_wallet": None, "address_check": None,
            "reasons": ["operator declared: none of the candidates is the debtor (no match)"],
        }
    else:
        try:
            match = match_account(client, fields, manual_uuid=company_uuid)
        except BOError as exc:
            return _identification_failed(parsed, fields, exc)

    if match.get("company_uuid") and not fields.get("company_uuid"):
        # Persist the resolved UUID back so it is visible in the parsed fields.
        parsed["company_uuid"] = match["company_uuid"]

    if match.get("needs_selection"):
        # Ambiguity is an operator decision — no scenario, no document, until a
        # candidate is picked (the UI re-runs with the chosen company_uuid).
        return {
            "status": "pending_selection",
            "parsed": parsed,
            "account": _account_public(match),
            "alerts": None, "balance": None, "seizure_check": None,
            "scenario": None, "plan": None, "amount": None, "declaration": None,
            "manual": build_manual(parsed=parsed, account=_account_public(match)),
            "warnings": ["account not uniquely resolved — pick a candidate or enter a company UUID"],
        }

    uuid = match.get("company_uuid") or ""

    # --- Step 4: alerts ---------------------------------------------------------
    alerts = checks_mod.check_alerts(client, uuid)
    if alerts.get("error"):
        warnings.append(f"alerts: {alerts['error']} (assumed none — scenario may be wrong)")

    # --- Step 5: ongoing seizures (own-case + junior aware) ---------------------
    sc = checks_mod.check_ongoing_seizures(
        client, uuid, ticket_case_ref=fields.get("case_references", ""))
    if sc.get("error") and uuid:
        warnings.append(f"seizures: {sc['error']}")
    own_rows = sc.get("ignored_same_case") or []
    if len(own_rows) > 1:
        warnings.append(
            f"{len(own_rows)} BO seizures matched this ticket's case reference "
            f"({', '.join(str(r.get('caseNumber') or r.get('id')) for r in own_rows)}) — "
            "verify they are all truly this case (possible duplicate or reference collision)")
    if sc.get("ignored_later"):
        warnings.append(
            f"{len(sc['ignored_later'])} junior Processing seizure(s) excluded (created after this case)")
    if sc.get("settling"):
        _captured = round(sum(float(s.get("seized_amount") or 0)
                              for s in sc["settling"]), 2)
        warnings.append(
            f"{len(sc['settling'])} seizure(s) pending transfer approval hold "
            f"{_captured:.2f} EUR captured funds — not competing Processing "
            "seizures, but subtracted from the available balance for closure coverage")

    # --- Step 6: balance (EUR-only; wallets already fetched during matching) ---
    if not uuid:
        balance_error = "account not resolved — balance check skipped"
    else:
        balance_error = match.get("wallets_error")  # None when the read worked
    bal = checks_mod.account_balance(match.get("wallets_items"), error=balance_error)
    if bal.get("non_eur"):
        warnings.append(
            f"{len(bal['non_eur'])} non-EUR wallet(s) excluded from available_eur — verify manually")

    # --- Step 7: scenario --------------------------------------------------------
    if kind == RFI_KIND:
        # Classified at ticket level: an RFI is never a seizure, whatever the
        # match/status say. The account data above still helps the operator
        # gather the requested information.
        scenario, notes = Scenario.RFI.value, list(cls_notes)
    elif kind in (REPEAL, RESTRICTION):
        # A document against an EXISTING seizure: the account + seizure data
        # above shows the operator WHICH seizure to refund/update; bigi itself
        # never writes, so the case routes out with specific guidance.
        scenario, notes = Scenario.ROUTED_OUT.value, list(cls_notes)
    else:
        checks = {"alerts": alerts, "seizures": sc, "balance": bal}
        scenario, notes = resolve_scenario(match, checks, fields)
    plan = build_plan(scenario, notes)
    warnings.extend(f"resolver: {n}" for n in notes)

    # --- Step 8: seized amount for the document ---------------------------------
    amount = compute_seized_amount(scenario, fields, bal, sc)
    warnings.extend(f"amount: {w}" for w in amount.get("warnings") or [])
    seized = amount.get("seized_eur")
    if seized is None and plan["template"] in ("T1", "T2", "T11"):
        warnings.append("amount: seized amount unknown — review [Seized amount] before sending")

    # --- Step 9: compose the document (guarded LLM, deterministic fallback) -----
    declaration = None
    template_id = plan["template"] or select_template(scenario)
    if template_id:
        # One German bullet per ongoing seizure, built from BO's structured
        # fields (creditor/date/amount) in the seizure check — the free-text
        # comment is only a fallback. compose() inserts them into [Comment].
        comments = [s.get("description_de") or s.get("comment", "") for s in sc.get("seizures", [])]
        text, composed_by = llm.compose(
            template_id,
            TEMPLATES[template_id],
            fields,
            comments,
            llm_config(db),
            seized_eur=seized,
            scenario=scenario,
        )
        declaration = {
            "template": template_id,
            "kind": template_kind(template_id),   # letter | email | guidance
            "text": text,
            "composed_by": composed_by,
            # Shown in the UI's "Mail subject" box; the body does not repeat it.
            "subject": build_subject(scenario, fields),
        }
    elif scenario == Scenario.ROUTED_OUT.value:
        warnings.append("routed out — no customer document is generated; handle manually")

    # --- Step 10: assemble -------------------------------------------------------
    # The seizable amount actually declared in the letter (mirrors build_context).
    if seized is not None:
        seizable_eur, seizable_eur_de = seized, de_amount(seized)
    else:
        seizable_eur, seizable_eur_de = None, None

    return {
        "status": "ok",
        "parsed": parsed,
        "account": _account_public(match),
        "alerts": _alerts_public(alerts),
        "balance": {
            **bal,
            "seizable_eur": seizable_eur,
            "seizable_eur_de": seizable_eur_de,
            # Funds held under Processing seizures (own case included) live on
            # the seizure record, not the wallets endpoint (which reads ~0).
            **checks_mod.held_funds(sc),
        },
        "seizure_check": sc,
        "scenario": scenario,
        "plan": plan,
        "amount": amount,
        "declaration": declaration,
        # Manual mode: the auto decisions as an editable set + recompose context.
        "manual": build_manual(
            parsed=parsed, scenario=scenario, plan=plan, declaration=declaration,
            account=_account_public(match),
            balance={**bal, "seizable_eur": seizable_eur},
            seizure_check=sc, wallets=match.get("wallets_items")),
        "warnings": warnings,
    }
