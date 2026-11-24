"""Scan job text for bond / fee / scam red flags (code, before any web research)."""
from __future__ import annotations

import re

# (flag, severity, regex). severity: reject = auto-reject; caution = you decide.
RULES: list[tuple[str, str, re.Pattern]] = [
    ("service_bond", "reject", re.compile(
        r"\b(service|training|employment)\s+(bond|agreement)\b|\bbond\s+(of|for)\s+\d|\b\d+[- ](year|yr|month)s?\s+bond\b|"
        r"\bsign(ing)?\s+(a|the)\s+bond\b|\bbond\s+period\b|\bbond\s+(is\s+)?(mandatory|required|applicable)\b", re.I)),
    ("minimum_service_period", "reject", re.compile(
        r"\bminimum\s+(service|commitment)\s+(period|of)\b|\bmust\s+(serve|stay|work)\s+(for\s+)?(at\s+least|a\s+minimum\s+of)\s+\d", re.I)),
    ("leaving_penalty", "reject", re.compile(
        r"\bpenalty\s+(if|in case|of|for)\s+(you\s+)?(leav|resign|quit|exit)|\bliquidated damages\b|"
        r"\bpay\s+back\s+(the\s+)?(training|cost)", re.I)),
    ("security_deposit", "reject", re.compile(
        r"\b(security|refundable|caution)\s+deposit\b|\bdeposit\s+(of\s+)?(rs\.?|inr|₹)", re.I)),
    ("candidate_fee", "reject", re.compile(
        r"\b(registration|processing|training|joining|application|placement|kit|certification|onboarding)\s+(fee|fees|charges?)\b|"
        r"\bpay\s+(a|the|us)?\s*(fee|amount)\b|\bfee\s+(is\s+)?(refundable|payable)\b", re.I)),
    ("original_documents", "reject", re.compile(
        r"\b(submit|deposit|retain|keep)\w*\s+(your\s+)?original\s+(certificates?|documents?|marksheets?)\b", re.I)),
    ("off_platform_contact", "caution", re.compile(
        r"\bwhats\s?app\b.{0,40}\b(resume|cv|apply)\b|\b(apply|send (your )?(resume|cv))\b.{0,40}\bwhats\s?app\b|"
        r"\btelegram\b", re.I)),
    ("free_email_recruiter", "caution", re.compile(
        r"\b[\w.+-]+@(gmail|yahoo|outlook|hotmail|rediffmail|ymail)\.(com|in)\b", re.I)),
    ("too_good_pay", "caution", re.compile(
        r"\b(earn|salary)\s+(up\s+to\s+)?(rs\.?|₹|inr)?\s*\d+(,\d+)*\s*(per|/)\s*(day|week)\b|\bwork from home\b.{0,60}\bearn\b", re.I)),
    ("undisclosed_client", "caution", re.compile(
        r"\b(our|a|the)\s+(client|customer)\s+(is\s+)?(a\s+)?(leading|top|reputed|fortune|well[- ]known|confidential)\b|"
        r"\bconfidential\s+(company|client|employer)\b|\bon behalf of (our|a) client\b", re.I)),
    ("unpaid", "caution", re.compile(r"\bunpaid\b|\bno\s+stipend\b|\bwithout\s+(pay|salary)\b", re.I)),
]


def scan(text: str) -> list[dict]:
    """-> [{flag, severity, quote}] — one entry per flag type, with the matching snippet."""
    found = []
    for flag, sev, rx in RULES:
        m = rx.search(text or "")
        if m:
            a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
            found.append({"flag": flag, "severity": sev, "quote": " ".join(text[a:b].split())})
    return found


def worst(flags: list[dict]) -> str | None:
    sev = {f["severity"] for f in flags}
    return "reject" if "reject" in sev else "caution" if "caution" in sev else None
