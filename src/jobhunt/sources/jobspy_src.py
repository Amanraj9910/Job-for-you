"""JobSpy-backed sources: Indeed, Glassdoor (your home country), Naukri (India only), LinkedIn (guest search only).

Registered sources: ``jobspy_indeed``, ``jobspy_glassdoor``, ``jobspy_naukri``, ``jobspy_linkedin``.

Helpers:
* ``scrape(site, query, location, ctx)`` -> (DataFrame | None, error | None)  one jobspy call
* ``row_to_job(row, site)``              -> Job | None   (finalized)
* ``df_to_jobs(df, site)``               -> list[Job]
* ``run_site(site, ctx)``                -> SourceResult (iterates ctx.queries x ctx.locations within budget)
* ``spread_pairs(queries, locations)``   -> (query, location) pairs ordered so a small budget covers every query

ToS posture: one board per call, small ``results_wanted``, a polite delay between calls (3-7 s for
LinkedIn/Naukri), never logged in, no proxies/stealth. A 406/429/captcha/"blocked" signal stops that
site for the run. LinkedIn & Naukri jobs are search/score/save only (channel manual_linkedin_naukri
unless jobspy surfaced the employer's own apply URL).
"""
from __future__ import annotations

import logging
import math
import random
import re
import time
from datetime import date, datetime

from ..geo import current, jobspy_country
from ..models import Job, RemoteType
from ..normalize import find_home_city, foreign_hint, ok_scopes, parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches

log = logging.getLogger(__name__)

# per-site knobs: extra scrape_jobs kwargs, default call budget, delay range between calls (seconds),
# max results per call, home_only (the site searches your home country: a job with no location is there),
# remote_flag (False: jobspy ignores is_remote for this site, so "remote" goes into the keywords).
# Request cost per call (python-jobspy 1.4.1): Indeed returns descriptions in the search response;
# Glassdoor fetches descriptions in one batched request per 25 jobs; Naukri fetches ONE DETAIL PAGE PER JOB
# with no delay in between, hence its small per-call cap; LinkedIn descriptions would be one page per job (off).
# ``country_indeed`` is filled from your profile's home country at call time (geo.jobspy_country()).
SITES: dict[str, dict] = {
    "indeed": {"kwargs": {"fetch_description": True}, "country": True,
               "budget": 6, "delay": (1.0, 2.0), "max": 30, "home_only": True},
    "glassdoor": {"kwargs": {"fetch_description": True}, "country": True,
                  "budget": 3, "delay": (1.5, 3.0), "max": 30, "home_only": True},
    "naukri": {"kwargs": {"fetch_description": True}, "budget": 2, "delay": (3.0, 7.0), "max": 10,
               "home_only": True, "only_cc": "IN"},
    "linkedin": {"kwargs": {"fetch_description": False}, "budget": 2, "delay": (3.0, 7.0), "max": 30,
                 "remote_flag": False},
}
MAX_RESULTS = 30
# jobspy error text that means "you are blocked / rate limited, stop for today"
STOP_RX = re.compile(r"\b(406|429)\b|captcha|rate[- ]?limit|too many requests|blocked", re.IGNORECASE)
_REMOTE_LOC = re.compile(r"\bremote\b", re.IGNORECASE)
_STATE_ONLY = re.compile(r"^[A-Z]{2}, [A-Za-z ]+$")   # Indeed's city-less "KA, India" / "BY, Germany"
_PERIOD = {"yearly": ("year", 1), "monthly": ("month", 1), "hourly": ("hour", 1),
           "weekly": ("year", 52), "daily": ("year", 260)}

_sleep = time.sleep  # patched in tests


def _val(v):
    """pandas cell -> plain python value (NaN/NaT/'' -> None)."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    if v.__class__.__name__ in ("NaTType", "NAType"):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _str(v) -> str | None:
    v = _val(v)
    return None if v is None else str(v)


def _float(v) -> float | None:
    v = _val(v)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _bool(v) -> bool | None:
    v = _val(v)
    if v is None:
        return None
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def _posted(v) -> datetime | None:
    v = _val(v)
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        v = v.isoformat()
    return parse_posted_at(v if isinstance(v, datetime) else str(v))


def _split_location(loc: str | None) -> tuple[str | None, bool]:
    """'Remote India' -> ('India', True); 'Bengaluru' -> ('Bengaluru', False)."""
    if not loc:
        return None, False
    if _REMOTE_LOC.search(loc):
        rest = _REMOTE_LOC.sub(" ", loc).strip(" ,-")
        return rest or None, True
    return loc, False


def spread_pairs(queries: list[str], locations: list[str | None]) -> list[tuple[str, str | None]]:
    """Every (query, location) pair exactly once, ordered in rounds: round r pairs query i with location
    (i + r) % n. A budget smaller than len(queries) * len(locations) therefore still reaches every query
    (and spreads locations) instead of spending it all on the first query's locations."""
    locs = list(locations or []) or [None]
    return [(queries[i], locs[(i + r) % len(locs)]) for r in range(len(locs)) for i in range(len(queries))]


def is_relevant(job: Job) -> bool:
    """Keep home-country jobs, unknown locations, and remote jobs open to your remote_ok_scopes.
    A remote job with no stated scope is kept unless its location names a foreign place."""
    if job.location_tier != 4:
        return True
    if job.remote_type == RemoteType.REMOTE:
        if job.remote_scope:
            return bool(set(job.remote_scope) & ok_scopes())
        return not foreign_hint(job.location or "")
    return False


def row_to_job(row, site: str, near: str | None = None) -> Job | None:
    """Map one jobspy DataFrame row (dict-like) to a finalized Job. None if unusable.

    ``near``: the home-country city that was searched. Indeed often gives only "KA, India"; such a
    location becomes "KA, India (near Bengaluru)" so the tier reflects the searched radius."""
    g = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    title, job_url = _str(g("title")), _str(g("job_url"))
    if not title or not job_url:
        return None
    direct = _str(g("job_url_direct"))
    jid = _str(g("id")) or job_url
    lo, hi = _float(g("min_amount")), _float(g("max_amount"))
    interval = (_str(g("interval")) or "").lower()
    period, mult = _PERIOD.get(interval, (None, 1))
    if mult != 1:
        lo = lo * mult if lo is not None else None
        hi = hi * mult if hi is not None else None
    if lo is None and hi is None:
        period = None
    wfh = _str(g("work_from_home_type"))  # naukri: "Work from office" / "Remote" / "Hybrid"
    location = _str(g("location"))
    if not location and SITES.get(site, {}).get("home_only"):
        location = current().home_name
    raw: dict = {"jobspy_id": jid, "site": site}
    if _str(g("company_url_direct")):
        raw["company_url_direct"] = _str(g("company_url_direct"))
    emails = _str(g("emails"))
    if emails:
        raw["emails"] = [e.strip() for e in emails.split(",") if e.strip()]
    job = Job(
        source=f"jobspy_{site}",
        source_job_id=jid,
        title=title,
        company=_str(g("company")) or "Unknown",
        source_url=job_url,
        apply_url=direct or job_url,
        location=location,
        posted_at=_posted(g("date_posted")),
        salary_min=lo, salary_max=hi,
        salary_currency=_str(g("currency")) if (lo or hi) else None,
        salary_period=period,
        jd_text=_str(g("description")) or "",
        employment_type=_str(g("job_type")),
        raw=raw,
    )
    w = (wfh or "").lower()
    remote = _bool(g("is_remote")) or ("remote" in w or "home" in w) or None
    if near and not remote and location and _STATE_ONLY.match(location):
        job.location = f"{location} (near {near})"
    if "hybrid" in w:
        job.remote_type = RemoteType.HYBRID
    elif remote:
        job.remote_type = RemoteType.REMOTE
    elif "office" in w:
        job.remote_type = RemoteType.ONSITE
    return finalize(job, jd_is_html=True, is_remote=remote, location_extra=wfh)


def df_to_jobs(df, site: str, near: str | None = None) -> list[Job]:
    if df is None or len(df) == 0:
        return []
    out = []
    for rec in df.to_dict("records"):
        try:
            j = row_to_job(rec, site, near)
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the batch
            log.warning("jobspy_%s: skipping row: %s", site, e)
            continue
        if j:
            out.append(j)
    return out


def scrape(site: str, query: str, location: str | None, ctx: SearchContext):
    """One jobspy call for one site. -> (DataFrame | None, error message | None). Never raises."""
    import jobspy  # lazy: heavy import, and lets tests monkeypatch jobspy.scrape_jobs

    loc, remote = _split_location(location)
    cfg = SITES[site]
    kw = dict(cfg["kwargs"])
    if cfg.get("country"):
        kw["country_indeed"] = jobspy_country()
    if remote and not cfg.get("remote_flag", True):
        # jobspy ignores is_remote here (LinkedIn): search your home country with "remote" in the keywords
        # instead of an unlocated worldwide search whose results are almost all filtered out as abroad
        query, loc = f"{query} remote", loc or current().home_name
    kw.update(site_name=[site], search_term=query, location=loc,
              results_wanted=max(1, min(ctx.limit, MAX_RESULTS, cfg.get("max", MAX_RESULTS))),
              hours_old=ctx.hours_old, description_format="html", verbose=0)
    if remote and cfg.get("remote_flag", True):
        kw["is_remote"] = True
    try:
        df = jobspy.scrape_jobs(**kw)
    except Exception as e:  # noqa: BLE001 — jobspy raises ValueError/requests/curl errors
        return None, f"{type(e).__name__}: {e}"
    errs = (getattr(df, "attrs", None) or {}).get("errors") or {}
    err = errs.get(site) or (next(iter(errs.values())) if errs else None)
    return df, (str(err) if err else None)


def run_site(site: str, ctx: SearchContext) -> SourceResult:
    name = f"jobspy_{site}"
    res = SourceResult(source=name, jobs=[])
    try:
        only = SITES[site].get("only_cc")
        if only and current().home_cc != only:
            return res          # e.g. Naukri is India-only: nothing to do for other home countries
        budget = int(ctx.budgets.get(name, SITES[site]["budget"]))
        if not ctx.queries:
            res.errors.append("no queries")
            return res
        pairs = spread_pairs(ctx.queries, ctx.locations)
        seen: set[str] = set()
        lo_d, hi_d = SITES[site]["delay"]
        for q, loc in pairs:
            if res.calls >= budget:
                break
            if res.calls:
                _sleep(random.uniform(lo_d, hi_d))
            res.calls += 1
            df, err = scrape(site, q, loc, ctx)
            searched, remote = _split_location(loc)
            near = find_home_city(searched) if searched and not remote else None
            for j in df_to_jobs(df, site, near):
                if j.source_job_id in seen:
                    continue
                seen.add(j.source_job_id)
                # search sources: queries already target titles; only the exclude list applies here
                if not title_matches(j.title, [], ctx.title_exclude) or not is_relevant(j):
                    continue
                res.jobs.append(j)
            if err:
                res.errors.append(f"{site} q={q!r} loc={loc!r}: {err}"[:500])
                if STOP_RX.search(err):
                    res.errors.append(f"{site}: stopped for this run (blocked/rate-limited)")
                    break
    except Exception as e:  # noqa: BLE001 — never raise
        res.errors.append(f"{site}: {type(e).__name__}: {e}"[:500])
    return res


@register("jobspy_indeed")
def search_indeed(ctx: SearchContext) -> SourceResult:
    return run_site("indeed", ctx)


@register("jobspy_glassdoor")
def search_glassdoor(ctx: SearchContext) -> SourceResult:
    return run_site("glassdoor", ctx)


@register("jobspy_naukri")
def search_naukri(ctx: SearchContext) -> SourceResult:
    return run_site("naukri", ctx)


@register("jobspy_linkedin")
def search_linkedin(ctx: SearchContext) -> SourceResult:
    return run_site("linkedin", ctx)
