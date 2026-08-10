"""Graded German address comparison (ticket one-liner vs BO structured address).

Grades — the confirmation step maps them to decisions:

* ``strong``   — postcode equal AND street similar (house number not conflicting)
* ``weak``     — postcode equal, but street missing/inconclusive or house differs
* ``mismatch`` — postcode differs, or same postcode with a CLEARLY different
                 street (the same-name-different-company case)
* ``unknown``  — postcode missing on either side (nothing reliable to compare)

Typo tolerance is deliberately one-directional: fuzzy similarity can only
upgrade weak->strong, never turn a hard mismatch into a match. Postcodes are
compared exactly (digits with a typo are dangerous in both directions).

Normalization handles the German conventions: umlaut folding (ä→ae, ß→ss),
``Straße/strasse/Str./-str.`` unified, punctuation/case ignored. Street
similarity uses stdlib ``difflib`` — no new dependencies.
"""
from __future__ import annotations

import difflib
import re

STRONG = "strong"
WEAK = "weak"
MISMATCH = "mismatch"
UNKNOWN = "unknown"

_POSTCODE_RE = re.compile(r"\b(\d{5})\b")
# House number: 16, 16a, 12-14, 3/1 …
_HOUSE_RE = re.compile(r"\b(\d{1,4})\s*([a-zA-Z]?)(?:\s*[-/]\s*(\d{1,4}))?\b")
_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
# Unify the street word wherever/however it is attached.
_STREET_WORD_RE = re.compile(r"str(?:a(?:ss|ß)e)?\.?", re.IGNORECASE)
# Floor/unit noise that BO addresses often carry ("II OG, Am Hehsel 38",
# "c/o Steuerbüro Meyer, Hauptstr. 1", "Whg. 4"): stripped before comparing.
_UNIT_TOKENS = frozenset({
    "og", "eg", "dg", "hh", "etage", "stock", "stockwerk", "geschoss",
    "whg", "wohnung", "app", "apartment", "appartement", "zi", "zimmer",
    "c", "o", "co", "bei", "raum", "buero", "haus", "geb", "gebaeude",
    "i", "ii", "iii", "iv", "v", "vi", "links", "rechts", "re", "li",
})


def _street_tokens(s: str) -> list[str]:
    return [t for t in _norm_street(s).split() if t not in _UNIT_TOKENS]


def _fold(s: str) -> str:
    s = str(s or "").strip().casefold()
    for k, v in _UMLAUTS.items():
        s = s.replace(k, v)
    return s


def _norm_street(s: str) -> str:
    """Normalized street name WITHOUT house numbers: folded, 'str' unified,
    punctuation collapsed."""
    s = _fold(s)
    s = _STREET_WORD_RE.sub("str", s)
    s = re.sub(r"\d+\s*[a-z]?(\s*[-/]\s*\d+\s*[a-z]?)?", " ", s)  # drop house nums
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _house_numbers(s: str) -> set[int]:
    """Every house number in the string; ranges expanded (12-14 -> 12,13,14)."""
    out: set[int] = set()
    for m in _HOUSE_RE.finditer(str(s or "")):
        a = int(m.group(1))
        b = int(m.group(3)) if m.group(3) else a
        if 0 < b - a <= 20:
            out.update(range(a, b + 1))
        else:
            out.add(a)
            if m.group(3):
                out.add(b)
    return out


def parse_ticket_address(raw: str) -> dict:
    """Split a Porters address one-liner into components.

    Shape observed live: "Ortenauer Str. 16, 77767, , Appenweier" —
    street+number first, a 5-digit postcode somewhere, city as the last
    non-empty non-postcode segment.
    """
    raw = str(raw or "")
    postcode = (_POSTCODE_RE.search(raw) or [None, ""])[1]
    segments = [p.strip() for p in raw.split(",") if p.strip()]
    street = ""
    for seg in segments:  # first segment with letters that isn't just the postcode
        if not re.search(r"[a-zA-ZäöüßÄÖÜ]", seg):
            continue
        # "60311 Frankfurt" is postcode+city, not a street: no house number
        # remains once the postcode is removed.
        rest = seg.replace(postcode or "\x00", " ").strip()
        if postcode and postcode in seg and not re.search(r"\d", rest):
            continue
        street = seg
        break
    city = ""
    for seg in reversed(segments):
        if seg == street:
            break
        if re.search(r"[a-zA-ZäöüßÄÖÜ]", seg) and not _POSTCODE_RE.fullmatch(seg):
            city = seg
            break
    return {"street": street, "postcode": postcode or "", "city": city,
            "houses": _house_numbers(street)}


def _bo_components(addr) -> dict:
    """Components from BO's structured overview address (dict) or a plain string.
    Accepts the field-name variants seen live/in fixtures (postCode/zip/postalCode)."""
    if isinstance(addr, dict):
        street = str(addr.get("street") or addr.get("address") or "")
        house = str(addr.get("houseNo") or addr.get("houseNumber") or "")
        postcode = str(addr.get("postCode") or addr.get("zip")
                       or addr.get("postalCode") or addr.get("postcode") or "").strip()
        city = str(addr.get("city") or "")
        if not _POSTCODE_RE.fullmatch(postcode):
            m = _POSTCODE_RE.search(" ".join(str(v) for v in addr.values()
                                             if isinstance(v, (str, int))))
            postcode = m.group(1) if m else ""
        houses = _house_numbers(house) or _house_numbers(street)
        return {"street": street, "postcode": postcode, "city": city, "houses": houses}
    return parse_ticket_address(str(addr or ""))


def compare_addresses(ticket_raw: str, bo_addr) -> dict:
    """Compare the ticket's address line against BO's structured address.

    Returns ``{grade, detail, ticket, account}`` — the two normalized
    "street house | postcode city" strings are included so the operator can
    see exactly what was compared.
    """
    t = parse_ticket_address(ticket_raw)
    b = _bo_components(bo_addr)

    def _disp(c: dict) -> str:
        left = " ".join(x for x in (_norm_street(c["street"]),
                                    "/".join(str(h) for h in sorted(c["houses"]))) if x)
        right = " ".join(x for x in (c["postcode"], _fold(c["city"])) if x)
        return f"{left} | {right}".strip(" |")

    out = {"ticket": _disp(t), "account": _disp(b)}

    if not t["postcode"] or not b["postcode"]:
        return {**out, "grade": UNKNOWN,
                "detail": "postcode missing on the "
                          + ("ticket" if not t["postcode"] else "account") + " side"}

    if t["postcode"] != b["postcode"]:
        return {**out, "grade": MISMATCH,
                "detail": f"postcode differs ({t['postcode']} vs {b['postcode']})"}

    t_tokens, b_tokens = _street_tokens(t["street"]), _street_tokens(b["street"])
    ts, bs = " ".join(t_tokens), " ".join(b_tokens)
    if not ts or not bs:
        return {**out, "grade": WEAK, "detail": "postcode matches; street not comparable"}

    ratio = difflib.SequenceMatcher(None, ts, bs).ratio()
    # Containment: the shorter street fully inside the longer one (extra
    # prefixes like c/o lines or building names must not break the match).
    short, long_ = (t_tokens, b_tokens) if len(ts) <= len(bs) else (b_tokens, t_tokens)
    contained = (len(" ".join(short)) >= 5 and short
                 and all(tok in long_ for tok in short))
    houses_conflict = bool(t["houses"] and b["houses"] and not (t["houses"] & b["houses"]))

    if ratio >= 0.85 or contained:
        if houses_conflict:
            return {**out, "grade": WEAK,
                    "detail": f"street matches (sim {ratio:.2f}) but house number differs"}
        return {**out, "grade": STRONG,
                "detail": "postcode + street match "
                          + (f"(sim {ratio:.2f})" if ratio >= 0.85 else "(street contained)")}
    if ratio <= 0.55:
        return {**out, "grade": MISMATCH,
                "detail": f"same postcode but clearly different street (sim {ratio:.2f})"}
    return {**out, "grade": WEAK,
            "detail": f"street similarity inconclusive (sim {ratio:.2f})"}
