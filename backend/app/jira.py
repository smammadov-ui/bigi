"""Jira helpers: Atlassian Document Format (ADF) flatten, webhook description
extraction, and read-only Jira REST API pulls (fetch one issue / search).

The Jira API token is used server-side only (Basic auth) and is NEVER logged.
"""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urlsplit

import httpx

from .schemas import BigiError, BigiUpstream

# ADF node types that introduce a block boundary -> a newline after their text.
_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "blockquote",
        "listItem",
        "bulletList",
        "orderedList",
        "codeBlock",
        "panel",
        "rule",
        "tableRow",
        "tableCell",
        "tableHeader",
        "mediaSingle",
    }
)


def flatten_adf(node) -> str:
    """Atlassian Document Format -> plain text.

    Recurses ``content``; emits ``text`` leaves; treats block nodes
    (``paragraph`` / ``heading`` / ``listItem`` / …) as block separators
    (a trailing newline); ``hardBreak`` -> newline. A plain string passes
    through unchanged. Robust to dicts / lists / ``None``.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_adf(child) for child in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type")

    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"

    inner = flatten_adf(node.get("content"))

    if node_type in _BLOCK_TYPES:
        # Block boundary: ensure the block's text is followed by a newline.
        return inner + "\n" if inner else "\n"
    return inner


def description_from_webhook(payload: dict) -> str:
    """Jira webhook JSON -> the issue description as plain text.

    Reads ``payload['issue']['fields']['description']`` which may be a plain
    string OR an ADF dict (flattened). Falls back to ``''`` if absent.
    """
    if not isinstance(payload, dict):
        return ""
    issue = payload.get("issue") or {}
    fields = issue.get("fields") or {}
    description = fields.get("description")
    if description is None:
        return ""
    if isinstance(description, str):
        return description.strip()
    return flatten_adf(description).strip()


def _basic_auth_header(email: str, api_token: str) -> str:
    raw = f"{email}:{api_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _require_jira_cfg(jira_cfg: dict) -> tuple[str, str, str]:
    base_url = (jira_cfg.get("base_url") or "").strip().rstrip("/")
    email = (jira_cfg.get("email") or "").strip()
    api_token = (jira_cfg.get("api_token") or "").strip()
    if not base_url or not email or not api_token:
        raise BigiError("Jira is not configured (base URL / email / API token)")
    return base_url, email, api_token


def _summary_text(fields: dict) -> str:
    summary = fields.get("summary")
    if isinstance(summary, str):
        return summary
    return flatten_adf(summary).strip() if summary else ""


_ISSUE_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*-\d+")

_UUID_TOKEN_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
# Porters write both "Definitive match: <uuid>" and "Definitive matches:
# <uuid>" (plural — live case FPOPCL-31102).
_LABELED_MATCH_RE = re.compile(
    r"(definitive|potential)\s+match(?:es)?\b[^\n]*", re.IGNORECASE
)
# Back-Office SEIZURE links Porters post in comments ("Backoffice URL to the
# created seizure: https://inhouse.finom.co/monitoring/seizures/<id>/..."):
# the UUID in such a URL is the seizure entity's ID, never a company.
_SEIZURE_URL_RE = re.compile(r"https?://\S*/seizures/\S*", re.IGNORECASE)


def extract_match_uuid_tiers(text: str) -> tuple[list[str], list[str], list[str]]:
    """``(definitive, potential, bare)`` company-UUID tiers from free text.

    UUIDs inside Back-Office seizure URLs are stripped BEFORE harvesting —
    they are seizure entity IDs, not companies (live case FPOPCL-31102, where
    the seizure link's UUID became a bogus picker candidate).
    """
    text = _SEIZURE_URL_RE.sub(" ", str(text or ""))
    definitive: list[str] = []
    potential: list[str] = []
    for m in _LABELED_MATCH_RE.finditer(text):
        bucket = definitive if m.group(1).lower() == "definitive" else potential
        bucket.extend(u.lower() for u in _UUID_TOKEN_RE.findall(m.group(0)))
    bare = [u.lower() for u in _UUID_TOKEN_RE.findall(text)]
    return definitive, potential, bare


def extract_match_uuids(text: str) -> list[str]:
    """Company UUIDs from free text (a Jira comment), best-first and deduped.

    Submitters have started posting the definitive/potential match UUIDs in
    COMMENTS instead of the description. Order of trust: UUIDs on a line
    labeled "definitive match" -> "potential match" -> any bare UUID token.
    Seizure-link UUIDs are ignored (see ``extract_match_uuid_tiers``).
    """
    definitive, potential, bare = extract_match_uuid_tiers(text)
    out: list[str] = []
    for u in definitive + potential + bare:
        if u not in out:
            out.append(u)
    return out


def fetch_comment_match_uuids(jira_cfg: dict, issue_key: str) -> list[str]:
    """Company UUIDs found in the issue's comments (read-only; [] on failure).

    Tiered across ALL comments: a labeled "definitive match" UUID in ANY
    comment wins outright over "potential match" UUIDs, which win over bare
    UUID tokens found elsewhere (Porters post the definitive match and a
    seizure link as SEPARATE comments — live case FPOPCL-31102). Newest
    comments win the ordering within each tier, so a submitter's correction
    outranks their earlier post.
    """
    key = normalize_issue_ref(issue_key)
    base_url, email, api_token = _require_jira_cfg(jira_cfg)
    url = f"{base_url}/rest/api/3/issue/{key}/comment"
    headers = {
        "Authorization": _basic_auth_header(email, api_token),
        "Accept": "application/json",
    }
    try:
        resp = httpx.get(url, headers=headers,
                         params={"maxResults": 50, "orderBy": "-created"}, timeout=30)
        if resp.status_code >= 400:
            return []
        comments = (resp.json() or {}).get("comments") or []
    except (httpx.HTTPError, ValueError):
        return []
    tiers: tuple[list[str], list[str], list[str]] = ([], [], [])
    for c in comments:
        body = c.get("body")
        text = body if isinstance(body, str) else flatten_adf(body)
        for tier, found in zip(tiers, extract_match_uuid_tiers(text)):
            tier.extend(found)
    for tier in tiers:
        dedup: list[str] = []
        for u in tier:
            if u not in dedup:
                dedup.append(u)
        if dedup:
            return dedup
    return []


def normalize_issue_ref(ref: str) -> str:
    """Turn a bare issue key or any Jira issue URL into an uppercase key.

    Handles /browse/KEY-1 links (query/fragment ignored), board links carrying
    ?selectedIssue=KEY-1, and service-desk queue/portal paths ending in the
    key. Anything unrecognized is returned as-is so fetch_issue can fail with
    its usual not-found error.
    """
    ref = (ref or "").strip()
    if not ref or "/" not in ref:
        return ref.upper() if _ISSUE_KEY_RE.fullmatch(ref) else ref
    parts = urlsplit(ref)
    for value in parse_qs(parts.query).get("selectedIssue", []):
        m = _ISSUE_KEY_RE.fullmatch(value.strip())
        if m:
            return m.group(0).upper()
    # Search the path first (never the hostname); fall back to query+fragment.
    matches = _ISSUE_KEY_RE.findall(parts.path) or _ISSUE_KEY_RE.findall(
        f"{parts.query} {parts.fragment}"
    )
    return matches[-1].upper() if matches else ref


def fetch_issue(jira_cfg: dict, issue_key: str) -> dict:
    """GET one issue's description + summary (Basic auth). Description flattened.

    Accepts a bare key or a pasted Jira issue link (see normalize_issue_ref).
    Returns ``{"key", "summary", "description"}``. Raises ``BigiError`` (400 for
    a bad request, 502 for an upstream/transport failure). Token never logged.
    """
    key = normalize_issue_ref(issue_key)
    if not key:
        raise BigiError("issue_key is required")
    base_url, email, api_token = _require_jira_cfg(jira_cfg)
    url = f"{base_url}/rest/api/3/issue/{key}"
    headers = {
        "Authorization": _basic_auth_header(email, api_token),
        "Accept": "application/json",
    }
    try:
        resp = httpx.get(
            url,
            headers=headers,
            params={"fields": "description,summary"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise BigiUpstream(f"Jira request failed: {type(exc).__name__}")
    if resp.status_code == 404:
        raise BigiError(f"Jira issue not found: {key}")
    if resp.status_code >= 400:
        body = (resp.text or "")[:2000]
        raise BigiUpstream(f"Jira returned HTTP {resp.status_code}: {body}")
    try:
        data = resp.json()
    except ValueError:
        raise BigiUpstream("Jira returned a non-JSON response")
    fields = data.get("fields") or {}
    return {
        "key": data.get("key", key),
        "summary": _summary_text(fields),
        "description": description_from_webhook({"issue": {"fields": fields}}),
    }


# Default Browse JQL — scoped to the FP-OPS-Claims (FPOPCL) service desk, where
# the seizure tickets live. Being project-scoped, it also satisfies the
# enhanced /search/jql endpoint's "must be bounded" rule. Override per-instance
# in Settings → Default JQL (Browse).
_DEFAULT_JQL = "project = FPOPCL ORDER BY created DESC"


def _ensure_bounded(jql: str) -> str:
    """Guarantee a restricting clause so ``/search/jql`` doesn't return 400.

    The enhanced endpoint rejects *unbounded* queries — an empty string, or a
    bare ``ORDER BY …`` with no restriction (the historical default) — with
    HTTP 400 "Unbounded JQL queries are not allowed here". When we detect that
    shape we prepend a 90-day ``created`` window so Browse / the connection
    test keep working with a legacy stored value. A query that already has a
    restriction is returned unchanged.
    """
    stripped = (jql or "").strip()
    if not stripped or stripped.lower().startswith("order by"):
        window = "created >= -90d"
        return f"{window} {stripped}".strip() if stripped else _DEFAULT_JQL
    return stripped


def search_issues(jira_cfg: dict, jql: str | None) -> list[dict]:
    """GET a small list of issues for the given (or configured default) JQL.

    Uses the enhanced search endpoint ``/rest/api/3/search/jql``. The legacy
    ``/rest/api/3/search`` was removed (Atlassian CHANGE-2046, HTTP 410) and
    the new endpoint also rejects *unbounded* JQL (HTTP 400) — so the query is
    passed through ``_ensure_bounded``. The response slice we rely on is
    unchanged — ``issues[] -> {key, fields.summary}`` — so the new
    ``nextPageToken`` / ``isLast`` paging keys are ignored (one bounded page).

    Returns ``[{"key", "summary"}]``. Read-only. Token never logged.
    """
    base_url, email, api_token = _require_jira_cfg(jira_cfg)
    effective_jql = _ensure_bounded(
        (jql or "").strip() or (jira_cfg.get("jql") or "").strip() or _DEFAULT_JQL
    )
    url = f"{base_url}/rest/api/3/search/jql"
    headers = {
        "Authorization": _basic_auth_header(email, api_token),
        "Accept": "application/json",
    }
    try:
        resp = httpx.get(
            url,
            headers=headers,
            params={"jql": effective_jql, "fields": "summary", "maxResults": 20},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise BigiUpstream(f"Jira request failed: {type(exc).__name__}")
    if resp.status_code >= 400:
        body = (resp.text or "")[:2000]
        raise BigiUpstream(f"Jira returned HTTP {resp.status_code}: {body}")
    try:
        data = resp.json()
    except ValueError:
        raise BigiUpstream("Jira returned a non-JSON response")
    issues = data.get("issues") or []
    out: list[dict] = []
    for it in issues:
        fields = it.get("fields") or {}
        out.append({"key": it.get("key", ""), "summary": _summary_text(fields)})
    return out
