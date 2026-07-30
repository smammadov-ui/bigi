"""Optional, strictly-bounded LLM compose — mini's guard discipline,
generalized to all 11 templates.

``compose`` fills the bracketed placeholders of a fixed German template. The
``[Comment]`` slot arrives pre-rendered (one "\\t• " bullet per ongoing
seizure, built from BO's structured fields, already German) and is inserted
verbatim; the LLM only translates a bullet that isn't German (raw-comment
fallback). It is strictly bounded: the LLM may only substitute placeholders —
it must not change, add, or remove legal sentences or bullet lines.

Acceptance guards (any failure -> ``templates.deterministic_fill``, never
raises):
  * non-empty output
  * per-template anchors survive (``templates.TEMPLATE_ANCHORS``)
  * no ``[bracket]`` placeholder remains
  * bullet-count invariant: output bullets == input comment bullets (catches
    silent merges/drops)
  * Anthropic safety refusal (HTTP 200 + stop_reason "refusal") -> fallback
  * surrounding code fences stripped

Raw httpx is used for both providers (keeps deps light). The api_key is never
logged. ``compose`` returns ``(text, composed_by)`` with composed_by in
{"llm:openai", "llm:anthropic", "deterministic"} for provenance.
"""
from __future__ import annotations

import json
import re

import httpx

from . import templates

_TIMEOUT = 30.0

_GUARDRAIL = (
    "You fill a fixed German legal template. Substitute ONLY the bracketed "
    "[placeholders] with the provided field values, each inserted VERBATIM. The "
    "[Comment] value is a pre-rendered block of tab-and-bullet (\"\\t• \") bullet "
    "lines, one per ongoing seizure — never merge, reword, reorder, drop, or add "
    "bullet lines, keep the leading \"\\t• \" bullet marker of each line unchanged, "
    "and keep every amount, date, and name exactly as given. If a "
    "comment is not in German, translate it to formal German but keep it on its own "
    "bullet line. Do NOT change, add, or remove any legal sentence or wording. Do "
    "not leave any [bracket] in the output. Output ONLY the final German plain-text "
    "declaration, no preamble, no code fences."
)

_OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
_ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"

_LEFTOVER_BRACKET_RE = re.compile(r"\[[A-Za-z][^\[\]]{0,60}\]")


def _build_user_message(template_id: str, template_body: str, fields: dict,
                        comments: list[str], seized_eur=None, scenario: str = "") -> str:
    """Build the user message: the verbatim template + all field values."""
    context = templates.build_context(template_id, fields, comments_de=comments,
                                      seized_eur=seized_eur, scenario=scenario)
    payload = {
        "template": template_body,
        "field_values": context,
    }
    return (
        "Fill the following German legal template. Replace each [placeholder] with "
        "the matching value from field_values, verbatim. The [Comment] value is a "
        "pre-rendered bullet block — keep one tab-and-bullet (\"\\t• \") line per "
        "bullet, in order, never merged or omitted, and keep the leading bullet "
        "marker; translate a bullet to formal German only if it is not already German.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _strip_fences(text: str) -> str:
    """Remove a surrounding triple-backtick code fence if present."""
    s = (text or "").strip()
    if s.startswith("```"):
        # Drop the first line (``` or ```lang) and a trailing fence line.
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _accept(template_id: str, text: str) -> bool:
    """Output guard: non-empty, per-template anchors survive, no leftover
    ``[placeholder]``."""
    if not text or not text.strip():
        return False
    for anchor in templates.TEMPLATE_ANCHORS.get(template_id, ()):
        if anchor not in text:
            return False
    if _LEFTOVER_BRACKET_RE.search(text):
        return False
    return True


def _call_openai(model: str, api_key: str, user: str) -> str:
    body = {
        "model": model or _OPENAI_DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": _GUARDRAIL},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_anthropic(model: str, api_key: str, user: str) -> str | None:
    body = {
        "model": model or _ANTHROPIC_DEFAULT_MODEL,
        "max_tokens": 2000,
        "system": _GUARDRAIL,
        "messages": [{"role": "user", "content": user}],
    }
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # A safety refusal returns HTTP 200 with stop_reason "refusal" — fall back.
    if data.get("stop_reason") == "refusal":
        return None
    return "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")


def compose(template_id: str, template_body: str, fields: dict, comments: list[str],
            llm_cfg: dict, seized_eur=None, scenario: str = "") -> tuple[str, str]:
    """Return ``(text, composed_by)``.

    ``composed_by`` is ``"llm:openai"``, ``"llm:anthropic"``, or ``"deterministic"``.
    ``llm_cfg`` = ``{provider, model, api_key}``. ``seized_eur`` is the computed
    seizable amount (see :mod:`app.amounts`) that fills ``[Seized amount]``;
    ``None`` falls back to the claim. Bounded to the template: fill placeholders
    only; translate ``comments`` into the ``[Comment]`` slot; do NOT change/add/
    remove legal sentences; output German plain text. Never raises — any failure
    falls back to ``templates.deterministic_fill``.
    """
    llm_cfg = llm_cfg or {}
    api_key = (llm_cfg.get("api_key") or "").strip()
    provider = (llm_cfg.get("provider") or "openai").strip()
    model = (llm_cfg.get("model") or "").strip()
    # One canonical comment list for the prompt, the bullet-count guard, and
    # the deterministic fallback alike.
    comments = [str(c).strip() for c in (comments or []) if str(c).strip()]

    def _fallback() -> tuple[str, str]:
        return (
            templates.deterministic_fill(template_id, fields, comments,
                                         seized_eur=seized_eur, scenario=scenario),
            "deterministic",
        )

    if not api_key:
        return _fallback()

    try:
        user = _build_user_message(template_id, template_body, fields, comments,
                                   seized_eur=seized_eur, scenario=scenario)
        if provider == "anthropic":
            raw = _call_anthropic(model, api_key, user)
            composed_by = "llm:anthropic"
        elif provider == "openai":
            raw = _call_openai(model, api_key, user)
            composed_by = "llm:openai"
        else:
            raw = None
            composed_by = "deterministic"

        if raw is None:
            raise ValueError("no usable LLM output")
        text = _strip_fences(raw)
        if not _accept(template_id, text):
            raise ValueError("LLM output failed anchor/empty/placeholder guard")
        # Every ongoing seizure must appear as its own bullet line — an LLM
        # that merged or dropped one produces a legally wrong declaration.
        bullets = sum(1 for line in text.split("\n") if line.lstrip().startswith("•"))
        if bullets != len(comments):
            raise ValueError("LLM output bullet count != ongoing-seizure comment count")
        return text, composed_by
    except Exception:
        # Any failure (transport, parse, refusal, empty, missing anchors,
        # leftover placeholders, merged/dropped comment bullets) -> fallback.
        return _fallback()
