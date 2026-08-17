# parse_spec.md — deterministic Jira parser rules

`app/parser.py` turns the raw text of a German seizure ticket (paste / Jira
Jira API pull) into a flat dict of parsed fields. It is **pure** —
string + regex work only, no LLM, no network. Imports only
`parse_date_iso` / `parse_decimal` from `app/formatting.py`.

## Public API

```python
def parse_jira(raw_text: str) -> dict:
    # {
    #   "fields": {<all PARSED_FIELDS>: str},   # absent -> ""
    #   "halted": bool,
    #   "halt_reasons": [str],
    #   "warnings": [str],
    #   "seized_iban_source": "provided" | "debtor_list" | "",
    # }

def norm_reg(s) -> str:
    # strip + drop all whitespace + upper-case. "HRB 990011" -> "HRB990011".
```

## PARSED_FIELDS (defined locally in parser.py)

```
company_uuid, company_uuid_candidates, seized_iban, debtor_ibans,
seizure_type, seizure_amount,
document_type, date_received, debtor_name, debtor_address, case_references,
creditor_bank, creditor_iban, creditor_bic, creditor_address, creditor_email,
creditor_name, comment, porters_document_id, issued_date,
debtor_dob, debtor_tax_id, debtor_register_number, debtor_legal_rep
```

Every key is always present in `fields`; an absent value is `""`.

## Field lines

- A field line is `key: value` with an **optional** leading bullet
  marker (`*`, `-`, or `•`) — real pastes often lose their bullets. The value
  may have no leading space.
- The key is lower-cased + trimmed and matched against an allowlist
  (`_KEY_MAP` plus the special keys `seized ibans`, `definitive match`,
  `potential match`). A colon-bearing line whose key is **not** in the
  allowlist is prose, not a field.
- Branch on a **non-empty** value: a present-but-empty `seized IBANs:` counts
  as absent.

### `_KEY_MAP` (lower-cased source key -> field)

| source key | field |
| --- | --- |
| `debtor list of ibans` | `debtor_ibans` |
| `seizure type` | `seizure_type` |
| `seizure amount` | `seizure_amount` |
| `document type` | `document_type` |
| `date received` | `date_received` |
| `debtor name` | `debtor_name` |
| `debtor address` | `debtor_address` |
| `case references` | `case_references` |
| `creditor bank` | `creditor_bank` |
| `creditor iban` | `creditor_iban` |
| `creditor bic` | `creditor_bic` |
| `creditor address` | `creditor_address` |
| `creditor email` | `creditor_email` |
| `porters document id/ new file name` | `porters_document_id` |
| `debtor date of birth` | `debtor_dob` |
| `debtor tax id` | `debtor_tax_id` |
| `debtor register number` | `debtor_register_number` |
| `debtor lr` | `debtor_legal_rep` |

Special keys (own handling, not in `_KEY_MAP`): `seized ibans` -> `seized_iban`,
`definitive match` / `potential match` -> `company_uuid`.

## Noise stripping

- A trailing `Additional information` / `Secondary information` suffix is removed
  from a value.
- A line that is **only** that section-title phrase is skipped entirely (neither
  a field nor a continuation).
- A line starting with `Third Party Declaration` **ends field parsing** — Porters
  tickets append a pre-rendered TPD template there, which is not ticket data (its
  colon-less first line would otherwise be glued onto the previous field as a
  continuation, e.g. corrupting an empty `seized IBANs:` into a false halt).

## Multi-line continuation

After the first field line, a non-empty line with **no recognized `key:`**
extends the previous field's value (internal whitespace collapsed to single
spaces). An **unrecognized `key:` line** (a colon line whose key is not in the
allowlist) resets the continuation anchor, so a later genuine continuation
cannot jump back over it to an earlier field. Blank lines are ignored.

## Prose extraction

- `creditor_name`: `request from (.+?) issued on` (case-insensitive; also accepts
  `seizure request from …`).
- `issued_date`: `issued on <date>` (ISO / `DD.MM.YYYY` / `DD/MM/YYYY`),
  normalised via `parse_date_iso`.
- `comment`: the leading prose **before the first field line** (noise stripped).
  If there is no field line, the whole text is the comment.

## Normalisation + halts

- `seizure_amount` -> `parse_decimal` -> `"{:.2f}"`. A non-empty value that
  cannot be parsed **halts** (and the field is cleared).
- `date_received`, `issued_date` -> `parse_date_iso` (ISO `YYYY-MM-DD`).
- `company_uuid`: tokens of `definitive match` + `potential match` (split on
  `[,;\s]+`) validated against the UUID format (8-4-4-4-12 hex); malformed
  tokens are dropped with a warning so they never reach BO. Exactly one
  distinct valid UUID -> `company_uuid`. Several (a field listing two, or both
  fields filled with different UUIDs) -> `company_uuid_candidates`
  (comma-joined, warning) — disambiguated at the checks step by comparing each
  candidate's BO wallet IBANs against the ticket's seized/debtor IBAN, else by
  operator selection. None is **not** a halt — a warning is added and the
  account is resolved later by search (register number / seized IBAN / name).
- `seized_iban`: provided `seized IBANs` -> else a single `debtor list of IBANs`
  entry. Validated against `^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$`.
  - empty -> **not** a halt (warning; derived from the matched account later).
  - a non-empty masked/invalid value -> **halt** (never guess).
  - multiple debtor IBANs -> **halt** (a human must select the seized account).
  - `seized_iban_source` reports where the value came from.
- `creditor_bic`: an invalid BIC (`^[A-Z0-9]{8}([A-Z0-9]{3})?$`) is a
  **warning**, not a halt; the raw value is retained.
