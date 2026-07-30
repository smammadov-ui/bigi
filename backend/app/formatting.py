"""German-locale formatting + robust number/date parsing.

Amounts: ``6.771,29`` (thousands ".", decimal ","). Dates: ``TT.MM.JJJJ``.
The v1 bug — ``"14,250.00 EUR"`` silently becoming ``0`` — is fixed in
``parse_decimal`` (handles US/German separators + currency text).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


def parse_decimal(value):
    """Parse a possibly messy money string into a float, or ``None``.

    Strips currency text/symbols; handles US (``14,250.00``) and German
    (``14.250,00``) conventions. Heuristic: the right-most separator is the
    decimal point; the other is grouping. Plain ``1250`` / ``1250.5`` pass through.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.\-]", "", str(value))  # drop EUR/€/$/USD/spaces
    if not s or s in ("-", ".", ","):
        return None
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # German: . groups, , decimal
        else:
            s = s.replace(",", "")                     # US: , groups, . decimal
    elif has_comma:
        parts = s.split(",")
        s = s.replace(",", ".") if (len(parts) == 2 and len(parts[1]) in (1, 2)) else s.replace(",", "")
    elif has_dot:
        parts = s.split(".")
        if not (len(parts) == 2 and len(parts[1]) in (1, 2)):
            s = s.replace(".", "")                     # grouping dots (14.250)
    try:
        return float(s)
    except ValueError:
        return None


def de_amount(value, default: str = "0,00") -> str:
    """Format a number German-style. ``"14,250.00 EUR" -> "14.250,00"``."""
    num = parse_decimal(value)
    if num is None:
        return default
    s = f"{num:,.2f}"  # US grouping: "6,771.29"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def parse_date_iso(value, default: str = "") -> str:
    """Normalize a date to ISO ``YYYY-MM-DD``.

    Accepts ``YYYY-MM-DD``, ``YYYY/MM/DD``, ``DD.MM.YYYY``, ``DD/MM/YYYY``.
    Distinguishes by which component is 4 digits. Unknown formats pass through.
    """
    if value is None or value == "":
        return default
    s = str(value).strip()
    m = re.match(r"^(\d{1,4})[-./](\d{1,2})[-./](\d{1,4})", s)
    if not m:
        return s
    a, b, c = m.groups()
    if len(a) == 4:      # YYYY?MM?DD
        y, mo, d = a, b, c
    elif len(c) == 4:    # DD?MM?YYYY
        d, mo, y = a, b, c
    else:
        return s
    try:
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return s


def de_date(value, default: str = "") -> str:
    """Format a date German-style ``TT.MM.JJJJ`` (accepts ISO / DD.MM.YYYY / epoch)."""
    if value is None or value == "":
        return default
    # epoch seconds OR milliseconds (int/float, or a long all-digit string)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit() and len(value.strip()) >= 9):
        try:
            ts = int(value)
            if abs(ts) >= 1_000_000_000_000:  # milliseconds
                ts //= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y")
        except (ValueError, OSError, OverflowError):
            return default
    iso = parse_date_iso(value)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso)
    if m:
        y, mo, d = m.groups()
        return f"{d}.{mo}.{y}"
    return str(value)


def iso_date_any(value, default: str = "") -> str:
    """Normalize ANY date representation to ISO ``YYYY-MM-DD`` (or ``default``).

    Handles epoch seconds/milliseconds (int/float or all-digit string — real BO
    returns ``accountStatusUpdated`` as an epoch integer), ISO strings (with or
    without time part), and DD.MM.YYYY / DD/MM/YYYY via ``parse_date_iso``.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit() and len(value.strip()) >= 9
    ):
        try:
            ts = int(value)
            if abs(ts) >= 1_000_000_000_000:  # milliseconds
                ts //= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return default
    s = str(value).strip()
    iso = parse_date_iso(s)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", iso)
    return m.group(1) if m else default
