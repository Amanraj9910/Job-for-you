"""Himalayas remote-jobs search API (A7).

Endpoint: GET https://himalayas.app/jobs/api/search?q=<query>&country=<name|ISO2>&page=<n>  (20 per page)
Verified live 2026-10: ``country=India`` and ``country=IN`` behave the same and return jobs restricted to India
*plus* jobs with no location restriction (worldwide). ``worldwide=true`` returns only unrestricted jobs and
overrides ``country``. Optional ``HIMALAYAS_API_KEY`` goes in ``Authorization: Bearer``.

ToS: applications must go through ``applicationLink`` and Himalayas must be credited, so ``apply_url`` is the
applicationLink and ``source_url`` is the Himalayas job page (guid).
"""
from __future__ import annotations

import re

from ..config import env
from ..geo import COUNTRY_NAMES, current, default_ok_scopes, scope_token
from ..models import Job, RemoteType
from ..normalize import ok_scopes, parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches

NAME = "himalayas"
SEARCH_URL = "https://himalayas.app/jobs/api/search"
PAGE_SIZE = 20
DEFAULT_BUDGET = 6  # HTTP calls per run (keyless quota is shared; data refreshes daily)

_PERIOD = {"hourly": "hour", "hour": "hour", "monthly": "month", "month": "month",
           "annual": "year", "annually": "year", "yearly": "year", "year": "year"}

_EU = {"Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Czech Republic", "Denmark", "Estonia",
       "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania",
       "Luxembourg", "Malta", "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain",
       "Sweden"}
_LATAM = {"Argentina", "Brazil", "Chile", "Colombia", "Costa Rica", "Mexico", "Peru", "Uruguay", "Ecuador",
          "Bolivia", "Paraguay", "Venezuela", "Guatemala", "Panama", "Dominican Republic"}
_COUNTRY_TOKEN = {"India": "IN", "United States": "US", "Canada": "CA", "United Kingdom": "UK",
                  "Australia": "AU", "New Zealand": "AU", "Singapore": "SG", "United Arab Emirates": "ME",
                  "Saudi Arabia": "ME", "Qatar": "ME"}


def _token(name: str) -> str | None:
    if name in _COUNTRY_TOKEN:
        return _COUNTRY_TOKEN[name]
    for cc, cname in COUNTRY_NAMES.items():
        if cname == name:
            return scope_token(cc)
    return None


def scope_tokens(restrictions: list[str] | None) -> list[str]:
    """Himalayas ``locationRestrictions`` (country names) -> remote_scope tokens.

    [] -> WORLDWIDE. A broad list (>= 10 countries) that includes your home country also gets your home
    region (e.g. APAC for India) so it passes the geo filters.
    """
    home = current()
    names = [n for n in (restrictions or []) if n]
    if not names:
        return ["WORLDWIDE"]
    out: list[str] = []

    def add(tok: str) -> None:
        if tok not in out:
            out.append(tok)

    if home.home_name in names:
        add(home.home_token)
        if len(names) >= 10:
            regs = [r for r in default_ok_scopes(home.home_cc) if r not in (home.home_token, "WORLDWIDE")]
            if regs:
                add(regs[0])
    for n in names:
        tok = _COUNTRY_TOKEN.get(n) or (_token(n) if n not in _EU and n not in _LATAM else None)
        if tok:
            add(tok)
        elif n in _EU:
            add("EU")
        elif n in _LATAM:
            add("LATAM")
        elif len(names) <= 5:
            add(re.sub(r"[^A-Z]+", "_", n.upper()).strip("_"))
    return out


def location_text(restrictions: list[str] | None) -> str:
    names = [n for n in (restrictions or []) if n]
    if not names:
        return "Remote (Worldwide)"
    home = current().home_name
    if home in names:
        return f"Remote - {home}" if len(names) == 1 else f"Remote ({home} +{len(names) - 1} more countries)"
    more = f" +{len(names) - 3} more" if len(names) > 3 else ""
    return f"Remote ({', '.join(names[:3])}{more})"


def safe_location(text: str | None) -> str:
    """Neutralize the English word "in" in a location string.

    Country codes are matched in capitals only, but "In" could still look like India's code to looser
    matchers, so lowercase/titlecase "in" -> "within" (e.g. "Anywhere in the World").
    """
    return re.sub(r"\b(in|In)\b", "within", text or "").strip()


def geo_ok(job: Job) -> bool:
    """Keep home-country jobs and remote jobs open to you (scope in remote_ok_scopes, or unknown scope).

    A remote job with no scope but a foreign place in its location (e.g. "Seattle, Washington") is dropped.
    Shared by the remote-board sources.
    """
    if job.location_tier in (1, 2, 3):
        return True
    scope = set(job.remote_scope or [])
    if scope:
        return bool(scope & ok_scopes())
    if job.remote_type == RemoteType.REMOTE:
        rest = re.sub(r"\b(remote|anywhere|worldwide|global|fully|100%|work from home|wfh)\b", " ",
                      (job.location or "").lower())
        return not re.sub(r"[^a-z]+", " ", rest).strip()
    return job.location_tier is None


def _headers() -> dict:
    key = env("HIMALAYAS_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def search_page(query: str, ctx: SearchContext, page: int = 1, country: str | None = None,
                worldwide: bool = False) -> dict:
    """One raw search call. Returns the API JSON ({jobs, totalCount, offset, limit, ...}); {} on 404."""
    params: dict = {"q": query}
    if worldwide:
        params["worldwide"] = "true"
    else:
        params["country"] = country or current().home_name
    if page > 1:
        params["page"] = page
    return ctx.http.get_json(SEARCH_URL, params=params, headers=_headers()) or {}


def parse_job(d: dict) -> Job | None:
    """Himalayas job dict -> finalized Job (None if unusable)."""
    title = (d.get("title") or "").strip()
    company = (d.get("companyName") or "").strip()
    guid = d.get("guid") or ""
    if not title or not company or not guid:
        return None
    slug = guid.rstrip("/").rsplit("/", 1)[-1]
    restrictions = d.get("locationRestrictions") or []
    job = Job(
        source=NAME,
        source_job_id=f"{d.get('companySlug') or company}/{slug}",
        title=title,
        company=company,
        source_url=guid,
        apply_url=d.get("applicationLink") or guid,
        location=location_text(restrictions),
        remote_type=RemoteType.REMOTE,
        remote_scope=scope_tokens(restrictions),
        posted_at=parse_posted_at(d.get("pubDate")),
        jd_text=d.get("description") or d.get("excerpt") or "",
        employment_type=d.get("employmentType"),
        raw={"guid": guid},
    )
    lo, hi = d.get("minSalary"), d.get("maxSalary")
    if lo or hi:
        job.salary_min = float(lo) if lo else None
        job.salary_max = float(hi) if hi else None
        job.salary_currency = d.get("currency") or None
        job.salary_period = _PERIOD.get((d.get("salaryPeriod") or "").lower(), "year")
    home = current()
    if home.home_name in restrictions and len(restrictions) == 1:
        job.country = home.home_cc
    return finalize(job, is_remote=True)


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(NAME, [])
    budget = int(ctx.budgets.get(NAME, DEFAULT_BUDGET))
    queries = ctx.queries or [""]
    seen: set[str] = set()
    for q in queries:
        page, total = 1, None
        while len(res.jobs) < ctx.limit and res.calls < budget:
            if total is not None and (page - 1) * PAGE_SIZE >= total:
                break
            try:
                res.calls += 1
                data = search_page(q, ctx, page=page)
            except Exception as e:  # noqa: BLE001 - a source must never raise
                res.errors.append(f"{NAME} q={q!r} page={page}: {type(e).__name__}: {e}")
                break
            items = data.get("jobs") or []
            total = int(data.get("totalCount") or 0)
            for d in items:
                if len(res.jobs) >= ctx.limit:
                    break
                if not title_matches(d.get("title") or "", ctx.title_include, ctx.title_exclude):
                    continue
                try:
                    job = parse_job(d)
                except Exception as e:  # noqa: BLE001
                    res.errors.append(f"{NAME} parse {d.get('guid')}: {type(e).__name__}: {e}")
                    continue
                if job and job.source_job_id not in seen and geo_ok(job):
                    seen.add(job.source_job_id)
                    res.jobs.append(job)
            if len(items) < PAGE_SIZE:
                break
            page += 1
        if len(res.jobs) >= ctx.limit or res.calls >= budget:
            break
    return res
