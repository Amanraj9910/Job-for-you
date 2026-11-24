"""Detect ATS from URLs and classify the apply channel (A15)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .models import Channel

# (ats_type, host regex, path regex capturing slug or None). Order matters.
_ATS_RULES: list[tuple[str, str, str | None]] = [
    ("greenhouse", r"(^|\.)(boards|job-boards)(\.eu)?\.greenhouse\.io$", r"^/(?:embed/job_app\?for=)?([^/?#]+)"),
    ("greenhouse", r"(^|\.)boards-api\.greenhouse\.io$", r"^/v1/boards/([^/?#]+)"),
    ("lever", r"^jobs(\.eu)?\.lever\.co$", r"^/([^/?#]+)"),
    ("ashby", r"^jobs\.ashbyhq\.com$", r"^/([^/?#]+)"),
    ("smartrecruiters", r"^(jobs|careers)\.smartrecruiters\.com$", r"^/([^/?#]+)"),
    ("workable", r"^apply\.workable\.com$", r"^/(?!j/)([^/?#]+)"),   # /j/<code> is a job shortlink, no slug
    ("workable", r"^([a-z0-9-]+)\.workable\.com$", None),
    ("recruitee", r"^([a-z0-9-]+)\.recruitee\.com$", None),
    ("bamboohr", r"^([a-z0-9-]+)\.bamboohr\.com$", None),
    ("breezy", r"^([a-z0-9-]+)\.breezy\.hr$", None),
    ("personio", r"^([a-z0-9-]+)\.jobs\.personio\.(com|de)$", None),
    ("teamtailor", r"^([a-z0-9-]+)\.teamtailor\.com$", None),
    ("jobvite", r"^jobs\.jobvite\.com$", r"^/([^/?#]+)"),
    ("keka", r"^([a-z0-9-]+)\.keka\.com$", None),
    ("zohorecruit", r"^([a-z0-9-]+)\.zohorecruit\.(com|in|eu)$", None),
    ("freshteam", r"^([a-z0-9-]+)\.freshteam\.com$", None),
    ("turbohire", r"(^|\.)turbohire\.co$", None),
    ("darwinbox", r"(^|\.)darwinbox\.(in|com)$", None),
    ("workday", r"(^|\.)myworkdayjobs\.com$|(^|\.)myworkdaysite\.com$|(^|\.)myworkday\.com$", None),
    ("successfactors", r"(^|\.)successfactors\.(com|eu)$|(^|\.)sapsf\.(com|eu)$", None),
    ("taleo", r"(^|\.)taleo\.net$", None),
    ("icims", r"(^|\.)icims\.com$", None),
    ("oracle", r"(^|\.)oraclecloud\.com$", None),
    ("eightfold", r"(^|\.)eightfold\.ai$", None),
    ("phenom", r"(^|\.)phenompeople\.com$", None),
    ("google_forms", r"^docs\.google\.com$", None),
]

AUTO_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "bamboohr", "breezy"}
PROBE_ATS = {"keka", "zohorecruit", "turbohire", "freshteam", "teamtailor", "personio", "jobvite"}
ACCOUNT_ATS = {"darwinbox", "workday", "successfactors", "taleo", "icims", "oracle", "eightfold", "phenom",
               "google_forms"}

BOARD_HOSTS = {
    "linkedin": "linkedin", "naukri": "naukri",
    "indeed": "board", "glassdoor": "board", "himalayas": "board", "remotive": "board", "remoteok": "board",
    "weworkremotely": "board", "foundit": "board", "monsterindia": "board", "shine": "board",
    "instahyre": "board", "cutshort": "board", "hirist": "board", "wellfound": "board", "angel": "board",
    "ycombinator": "board", "workatastartup": "board", "internshala": "board", "timesjobs": "board",
    "apna": "board", "ziprecruiter": "board", "google": "board", "simplyhired": "board", "jooble": "board",
    "adzuna": "board", "careerjet": "board", "talent": "board", "dice": "board", "builtin": "board",
    "otta": "board", "welcometothejungle": "board", "jobicy": "board", "arbeitnow": "board",
    "hnhiring": "board", "news": "board", "iimjobs": "board", "updazz": "board", "freshersworld": "board",
}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def detect_ats(url: str | None) -> tuple[str | None, str | None]:
    """Return (ats_type, slug) from an apply/career URL, or (None, None)."""
    if not url:
        return None, None
    p = urlparse(url)
    host = (p.hostname or "").lower()
    path_q = p.path + (f"?{p.query}" if p.query else "")
    # company sites embedding Greenhouse: ?gh_jid=123 (slug unknown)
    if "gh_jid" in parse_qs(p.query):
        return "greenhouse", None
    for ats, host_rx, path_rx in _ATS_RULES:
        hm = re.search(host_rx, host)
        if not hm:
            continue
        slug = None
        if path_rx:
            pm = re.search(path_rx, path_q)
            slug = pm.group(1) if pm else None
        elif hm.groups() and hm.group(1) and hm.group(1) not in ("", "."):
            slug = hm.group(1)
        if ats == "greenhouse" and p.path.startswith("/embed/job_app"):
            slug = (parse_qs(p.query).get("for") or [slug])[0]
        if ats == "google_forms" and "/forms/" not in p.path:
            continue
        return ats, slug
    return None, None


def board_kind(url: str | None) -> str | None:
    """'linkedin' / 'naukri' / 'board' if the URL is a job board or aggregator, else None."""
    host = _host(url or "")
    if not host:
        return None
    labels = host.split(".")
    for lab in labels:
        if lab in BOARD_HOSTS:
            if lab == "google" and "/forms/" in (urlparse(url).path or ""):
                return None
            if lab == "news" and "ycombinator" not in host:
                continue
            return BOARD_HOSTS[lab]
    return None


def classify_channel(apply_url: str | None, *, source: str | None = None) -> tuple[Channel, str | None, str | None]:
    """-> (channel, ats_type, ats_slug). Follows the A15 table; redirects should be resolved by the caller."""
    if apply_url and apply_url.lower().startswith("mailto:"):
        return Channel.MANUAL_EMAIL, None, None
    ats, slug = detect_ats(apply_url)
    if ats in AUTO_ATS:
        return Channel.AUTO, ats, slug
    if ats in PROBE_ATS:
        return Channel.AUTO_PROBE, ats, slug
    if ats in ACCOUNT_ATS:
        return Channel.MANUAL_ACCOUNT_PORTAL, ats, slug
    kind = board_kind(apply_url)
    src = (source or "").lower()
    if kind in ("linkedin", "naukri") or (not apply_url and ("linkedin" in src or "naukri" in src)):
        return Channel.MANUAL_LINKEDIN_NAUKRI, None, None
    if kind == "board" or not apply_url:
        return Channel.MANUAL_OTHER, None, None
    # an employer's own career page with an unknown form
    return Channel.AUTO_PROBE, None, None
