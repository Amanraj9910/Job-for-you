"""Normalizers: location / remote scope / tier, experience, salary, posted date."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from dateutil import parser as dtparser

# ---------------------------------------------------------------- location
# Country-specific knowledge lives in geo.py and follows the user's profile (home country, cities, scopes).
from .geo import INDIA_CITIES, find_home_city, foreign_hint, is_home, ok_scopes, remote_scope  # noqa: E402,F401
from .models import RemoteType

_REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed|home[- ]based|telecommute)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ONSITE = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based)\b", re.I)


def remote_type(text: str | None, is_remote_flag: bool | None = None) -> RemoteType:
    t = text or ""
    if _HYBRID.search(t):
        return RemoteType.HYBRID
    if is_remote_flag or _REMOTE.search(t):
        return RemoteType.REMOTE
    if _ONSITE.search(t):
        return RemoteType.ONSITE
    return RemoteType.UNKNOWN


def location_tier(location: str | None, country: str | None, rtype: RemoteType, scope: list[str]) -> int | None:
    """1 = first-choice city, 2 = second-choice city, 3 = anywhere else in your home country (or remote open
    to it), 4 = abroad, None = unknown. Cities and country come from the profile (see geo.py)."""
    from .geo import current
    g = current()
    city = find_home_city(location)
    if city:
        return g.tier_of_city(city)
    if is_home(location, country):
        return 3
    if rtype == RemoteType.REMOTE and scope and set(scope) <= ok_scopes() and g.home_token in scope:
        return 3
    if location or country or scope:
        return 4
    return None


def normalize_location(location: str | None, country: str | None = None, *, is_remote: bool | None = None,
                       extra_text: str | None = None) -> dict:
    """Return dict(location, country, remote_type, remote_scope, location_tier)."""
    from .geo import current
    g = current()
    loc = (location or "").strip() or None
    blob = " ".join(x for x in (loc, extra_text) if x)
    rtype = remote_type(blob, is_remote)
    scope = remote_scope(blob) if rtype == RemoteType.REMOTE or _REMOTE.search(blob) else []
    if not country:
        if is_home(loc):
            country = g.home_cc
    if rtype == RemoteType.REMOTE and not scope and loc and is_home(loc):
        scope = [g.home_token]
    tier = location_tier(loc, country, rtype, scope)
    return {"location": loc, "country": country, "remote_type": rtype, "remote_scope": scope,
            "location_tier": tier}


# ---------------------------------------------------------------- experience
_NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "zero": 0}
_YRS = r"(?:years?|yrs?\.?|yoe)"
_DASH = r"\s*(?:-|–|—|to)\s*"
_EXP_PATTERNS = [
    re.compile(rf"(\d+(?:\.\d+)?){_DASH}(\d+(?:\.\d+)?)\s*\+?\s*{_YRS}", re.I),                         # 2-4 years
    re.compile(rf"(?:minimum|min\.?|at least|atleast)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*\+?\s*{_YRS}", re.I),  # min 3 yrs
    re.compile(rf"(\d+(?:\.\d+)?)\s*\+\s*{_YRS}", re.I),                                                  # 3+ years
    re.compile(rf"(\d+(?:\.\d+)?)\s*(?:or more|plus)\s*{_YRS}", re.I),
    re.compile(rf"(\d+(?:\.\d+)?)\s*{_YRS}\s*(?:of\s*)?(?:\w+\s){{0,4}}?(?:experience|exp\b)", re.I),      # 3 years of exp
    re.compile(rf"\b({'|'.join(_NUMW)})\s*\(?\d*\)?\s*\+?\s*{_YRS}", re.I),                               # three years
]
_FRESHER = re.compile(r"\b(fresher|freshers|entry[- ]level|new grad|graduate program|0 years)\b", re.I)


def _num(s: str) -> float:
    s = s.lower()
    return float(_NUMW[s]) if s in _NUMW else float(s)


def parse_experience(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    t = text.replace("–", "-").replace("—", "-")
    for i, rx in enumerate(_EXP_PATTERNS):
        m = rx.search(t)
        if not m:
            continue
        if i == 0:
            lo, hi = _num(m.group(1)), _num(m.group(2))
            if lo <= hi <= 40:
                return lo, hi
            continue
        v = _num(m.group(1))
        if v <= 40:
            return v, None
    if _FRESHER.search(t):
        return 0.0, 1.0
    return None, None


# ---------------------------------------------------------------- salary
# Rough FX rates (units of USD per 1 unit of currency) — only used to compare a stated salary with your floor.
FX_TO_USD = {"USD": 1.0, "INR": 1 / 84.0, "EUR": 1.08, "GBP": 1.28, "SGD": 0.75, "AED": 0.27, "CAD": 0.73,
             "AUD": 0.66, "NZD": 0.60, "CHF": 1.13, "SEK": 0.095, "NOK": 0.094, "DKK": 0.145, "PLN": 0.25,
             "CZK": 0.043, "HUF": 0.0027, "RON": 0.22, "JPY": 0.0067, "KRW": 0.00073, "CNY": 0.14, "HKD": 0.128,
             "TWD": 0.031, "PHP": 0.017, "IDR": 0.000062, "MYR": 0.22, "THB": 0.028, "VND": 0.00004,
             "PKR": 0.0036, "BDT": 0.0084, "LKR": 0.0033, "NPR": 0.0075, "BRL": 0.18, "MXN": 0.055, "ARS": 0.001,
             "COP": 0.00025, "CLP": 0.00105, "NGN": 0.00065, "KES": 0.0077, "ZAR": 0.055, "EGP": 0.02,
             "ILS": 0.27, "TRY": 0.029, "SAR": 0.27, "QAR": 0.27, "UAH": 0.024}
FX_TO_INR = {k: v / FX_TO_USD["INR"] for k, v in FX_TO_USD.items()}   # kept for older callers
_CUR = [("INR", re.compile(r"₹|\binr\b|\brs\.?\b|\brupees?\b|\blpa\b|\blakhs?\b|\blacs?\b|\bcrores?\b", re.I)),
        ("USD", re.compile(r"\$|\busd\b", re.I)), ("EUR", re.compile(r"€|\beur\b", re.I)),
        ("GBP", re.compile(r"£|\bgbp\b", re.I)), ("SGD", re.compile(r"\bsgd\b", re.I)),
        ("AED", re.compile(r"\baed\b", re.I))]
_AMT = r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(k|l|lpa|lakhs?|lacs?|cr|crores?|m|mn)?"
_SAL_RANGE = re.compile(rf"(?:₹|\$|€|£|inr|usd|rs\.?)?\s*{_AMT}\s*(?:-|–|—|to)\s*(?:₹|\$|€|£|inr|usd|rs\.?)?\s*{_AMT}", re.I)
_SAL_ONE = re.compile(rf"(?:up ?to|upto|max(?:imum)?|ctc|salary|₹|\$|€|£|inr|usd|rs\.?)[:\s]*{_AMT}", re.I)
_MONTH = re.compile(r"(per month|/ ?month|/mo\b|monthly|p\.m\.|pm\b|a month)", re.I)
_HOUR = re.compile(r"(per hour|/ ?hr|/ ?hour|hourly)", re.I)


def _amount(num: str, unit: str | None) -> float:
    v = float(num.replace(",", ""))
    u = (unit or "").lower()
    if u in ("l", "lpa", "lakh", "lakhs", "lac", "lacs"):
        return v * 100_000
    if u in ("cr", "crore", "crores"):
        return v * 10_000_000
    if u == "k":
        return v * 1_000
    if u in ("m", "mn"):
        return v * 1_000_000
    return v


def parse_salary(text: str | None, default_currency: str | None = None
                 ) -> tuple[float | None, float | None, str | None, str | None]:
    """-> (min, max, currency, period) with amounts per *period* in currency units. period in year/month/hour."""
    if not text:
        return None, None, None, None
    t = text.replace("–", "-").replace("—", "-")
    cur = next((c for c, rx in _CUR if rx.search(t)), default_currency)
    period = "hour" if _HOUR.search(t) else "month" if _MONTH.search(t) else "year"

    def money_like(s: str, units: tuple) -> bool:
        return bool(re.search(r"[₹$€£]|\b(inr|usd|eur|gbp|rs)\b", s, re.I) or any(units)
                    or default_currency)

    lo = hi = None
    for m in _SAL_RANGE.finditer(t):
        u1, u2 = m.group(2), m.group(4)
        if not money_like(m.group(0), (u1, u2)):
            continue          # e.g. "2-4 years" is not a salary
        lo, hi = _amount(m.group(1), u1 or u2), _amount(m.group(3), u2 or u1)
        break
    if hi is None:
        m = _SAL_ONE.search(t)
        if m:
            hi = _amount(m.group(1), m.group(2))
            lo = None if re.search(r"up ?to|upto|max", m.group(0), re.I) else hi
        else:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(lpa|lakhs?|lacs?)\b", t, re.I)   # "10 Lakhs p.a."
            if not m:
                return None, None, cur, None
            lo = hi = _amount(m.group(1), m.group(2))
            cur = cur or "INR"
    if (hi or lo or 0) < 50 and cur != "INR":
        return None, None, cur, None  # almost certainly not a salary
    return lo, hi, cur, period


def annual_in(amount: float | None, currency: str | None, period: str | None, target: str = "INR",
              default_currency: str = "INR") -> float | None:
    """Amount converted to a yearly figure in ``target`` currency (None if unknown currency)."""
    if amount is None:
        return None
    src = FX_TO_USD.get((currency or default_currency).upper())
    dst = FX_TO_USD.get((target or "USD").upper())
    if src is None or dst is None:
        return None
    mult = {"year": 1, "month": 12, "hour": 2000}.get(period or "year", 1)
    return amount * mult * src / dst


def annual_inr(amount: float | None, currency: str | None, period: str | None) -> float | None:
    return annual_in(amount, currency, period, "INR")


# ---------------------------------------------------------------- dates
_REL = re.compile(r"(\d+)\+?\s*(minute|min|hour|hr|day|week|month)s?\s*ago", re.I)


def parse_posted_at(value, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:          # epoch ms
            v /= 1000
        if v < 1e8:
            return None
        return datetime.fromtimestamp(v, tz=UTC)
    s = str(value).strip()
    if s.isdigit():
        return parse_posted_at(int(s), now)
    low = s.lower()
    if low in ("today", "just posted", "just now", "new", "few hours ago") or "hour" in low and "ago" in low \
            and not _REL.search(low):
        return now
    if low == "yesterday":
        return now - timedelta(days=1)
    m = _REL.search(low)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "hr": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n),
                 "month": timedelta(days=30 * n)}[unit]
        return now - delta
    try:
        d = dtparser.parse(s)
    except (ValueError, OverflowError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)
