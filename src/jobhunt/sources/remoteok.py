"""RemoteOK public feed (A9).

Endpoint: GET https://remoteok.com/api  -> full feed (~100 jobs, ~670 KB); element 0 is the legal notice.
ToS: link back to the Remote OK URL and mention Remote OK; undocumented limits, so a few calls a day at most
(rolling cap in ``data/cache/remoteok_calls.json``). ``salary_min/max == 0`` means "not listed".
The feed text is often double-encoded UTF-8 (mojibake like ``â\\x80\\x99``); ``fix_mojibake`` repairs it.
"""
from __future__ import annotations

import html
import re

from ..models import Job, RemoteType
from ..normalize import parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches
from .himalayas import geo_ok, safe_location
from .remotive import DailyCap

NAME = "remoteok"
API_URL = "https://remoteok.com/api"
DEFAULT_PER_DAY = 4

_MOJI = re.compile("[Â-ô][\u0080-¿]")
_STOP = {"and", "the", "for", "with", "remote", "of", "in", "a", "an", "to"}


def fix_mojibake(s: str | None) -> str:
    if not s or not _MOJI.search(s):
        return s or ""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _plain(s: str | None) -> str:
    return html.unescape(fix_mojibake(s)).strip()


def query_match(text: str, queries: list[str]) -> bool:
    """True if every significant word of any query appears (word-boundary, case-insensitive) in text."""
    if not queries:
        return True
    t = (text or "").lower()
    for q in queries:
        words = [w for w in re.findall(r"[a-z0-9+#.]+", q.lower()) if w not in _STOP and len(w) > 1]
        if words and all(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", t) for w in words):
            return True
    return False


def wanted(title: str, tags: list[str] | None, ctx: SearchContext) -> bool:
    """Title include/exclude filter; when no include list is given, fall back to ctx.queries on title+tags."""
    if not title_matches(title, ctx.title_include, ctx.title_exclude):
        return False
    if ctx.title_include:
        return True
    return query_match(f"{title} {' '.join(tags or [])}", ctx.queries)


def fetch_feed(ctx: SearchContext) -> list[dict]:
    """One raw call; returns job dicts with the legal-notice element removed."""
    data = ctx.http.get_json(API_URL) or []
    return [d for d in data if isinstance(d, dict) and d.get("id") and d.get("position")]


def parse_job(d: dict) -> Job | None:
    title = _plain(d.get("position"))
    company = _plain(d.get("company"))
    if not title or not company:
        return None
    loc = safe_location(_plain(d.get("location")))
    job = Job(
        source=NAME,
        source_job_id=str(d["id"]),
        title=title,
        company=company,
        source_url=d.get("url"),
        apply_url=d.get("apply_url") or d.get("url"),
        location=loc or "Remote",
        remote_type=RemoteType.REMOTE,
        posted_at=parse_posted_at(d.get("epoch") or d.get("date")),
        jd_text=fix_mojibake(d.get("description")),
        raw={"id": d.get("id")},
    )
    lo, hi = d.get("salary_min") or 0, d.get("salary_max") or 0
    if lo or hi:
        job.salary_min = float(lo) if lo else None
        job.salary_max = float(hi) if hi else None
        job.salary_currency, job.salary_period = "USD", "year"
    return finalize(job, is_remote=True)


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(NAME, [])
    try:
        cap = DailyCap(NAME, ctx.budgets.get("remoteok_per_day", DEFAULT_PER_DAY))
        if int(ctx.budgets.get(NAME, 1)) <= 0:
            return res
        if cap.remaining() <= 0:
            res.errors.append(f"{NAME}: daily cap reached ({cap.per_day}/24h; see {cap.path.name})")
            return res
        cap.record()
        res.calls += 1
        items = fetch_feed(ctx)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"{NAME}: {type(e).__name__}: {e}")
        return res
    for d in items:
        if len(res.jobs) >= ctx.limit:
            break
        try:
            if not wanted(_plain(d.get("position")), d.get("tags"), ctx):
                continue
            job = parse_job(d)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{NAME} parse {d.get('id')}: {type(e).__name__}: {e}")
            continue
        if job and geo_ok(job):
            res.jobs.append(job)
    return res
