"""Keka Hire careers portals (India-heavy ATS; channel auto_probe via the classifier).

Found by inspecting real portals (eccouncil / skit / nurix / mobisy .keka.com, Oct 2026):

List (primary):  GET https://{slug}.keka.com/careers/api/jobs/default/active
                 the call the default careers page (cdn.keka.com/careers/.../app.min.js) makes; JSON list of
                 active jobs **with full description HTML**: {id, title, description, departmentName,
                 jobLocations[{name, city, state, countryCode, countryName}], experience ("4-6", "3+"),
                 salaryRange {minimum, maximum, currency, salaryPeriod}, salaryRangeFormat, publishedOn,
                 skillNames, jobType, jobNumber}
List (fallback): GET https://{slug}.keka.com/careers/api/embedjobs/default/active/{portalGuid}
                 used by custom career-portal pages / the website embed widget. The GUID is resolved from
                 the careers page HTML (``/ats/documents/{guid}/careerportal/...`` or ``embedjobs/js/{guid}``).
Company:         GET https://{slug}.keka.com/careers/api/organization/default/careerportalinfo -> {"name"}
Apply:           https://{slug}.keka.com/careers/jobdetails/{id}
Bad slug:        302 -> /careers/Content/TenantNotFound.html ; tenant without a public portal:
                 302 -> /careers/Content/403.html (redirects are not followed).
salaryPeriod:    0 = per year (INR 8,00,000 - 10,00,000), 3 = per month (MYR 5,000 - 7,000); others unknown.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..models import Job
from ..normalize import normalize_location
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .workable import BudgetExhausted, Calls, keep_location, note_error, pretty_company, run_boards

NAME = "keka"
_GUID = re.compile(r"(?:/ats/documents/|embedjobs/js/|embedjobs/[\w-]+/active/)"
                   r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_PERIOD = {0: "year", 3: "month"}
_EXP_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)")
_EXP_MIN = re.compile(r"(\d+(?:\.\d+)?)")


def host_for(board: str) -> str:
    """'skit' | 'skit.keka.com' | 'https://skit.keka.com/careers' -> 'skit.keka.com'."""
    b = (board or "").strip()
    if "://" in b:
        b = urlparse(b).hostname or ""
    b = b.split("/")[0].lower()
    return b if b.endswith(".keka.com") else f"{b}.keka.com"


def slug_of(board: str) -> str:
    return host_for(board).removesuffix(".keka.com")


def _redirect_error(slug: str, r) -> SourceError:
    loc = r.headers.get("location") or ""
    if "TenantNotFound" in loc:
        return SourceError(f"{NAME}:{slug}: not_found (TenantNotFound)")
    if "403" in loc:
        return SourceError(f"{NAME}:{slug}: careers_portal_disabled (403)")
    return SourceError(f"{NAME}:{slug}: not_found (redirect {r.status_code} -> {loc})")


def _get(calls: Calls | None, ctx: SearchContext, url: str):
    kw = {"follow_redirects": False}
    return calls.get(url, **kw) if calls else ctx.http.get(url, **kw)


def resolve_portal_guid(board: str, ctx: SearchContext, calls: Calls | None = None) -> str | None:
    """Portal GUID from the careers page HTML (or the custom portal document it loads)."""
    host, slug = host_for(board), slug_of(board)
    r = _get(calls, ctx, f"https://{host}/careers")
    if 300 <= r.status_code < 400:
        raise _redirect_error(slug, r)
    m = _GUID.search(r.text or "")
    return m.group(1) if m else None


def list_active(board: str, ctx: SearchContext, calls: Calls | None = None) -> list[dict]:
    """Active jobs (with JD). Raises SourceError for unknown tenants / disabled portals."""
    host, slug = host_for(board), slug_of(board)
    r = _get(calls, ctx, f"https://{host}/careers/api/jobs/default/active")
    if 300 <= r.status_code < 400:
        raise _redirect_error(slug, r)
    data = None
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            data = None
    if isinstance(data, list):
        return data
    # fallback: the embed-widget endpoint keyed by the portal GUID
    guid = resolve_portal_guid(board, ctx, calls)
    if not guid:
        raise SourceError(f"{NAME}:{slug}: no job list (HTTP {r.status_code}) and no portal GUID found")
    r2 = _get(calls, ctx, f"https://{host}/careers/api/embedjobs/default/active/{guid}")
    if 300 <= r2.status_code < 400:
        raise _redirect_error(slug, r2)
    try:
        data = r2.json()
    except ValueError as e:
        raise SourceError(f"{NAME}:{slug}: embedjobs returned non-JSON (HTTP {r2.status_code})") from e
    if not isinstance(data, list):
        raise SourceError(f"{NAME}:{slug}: unexpected payload")
    return data


def company_name(board: str, ctx: SearchContext, calls: Calls | None = None) -> str | None:
    host = host_for(board)
    try:
        r = _get(calls, ctx, f"https://{host}/careers/api/organization/default/careerportalinfo")
        if r.status_code == 200:
            d = r.json()
            return (d.get("name") or d.get("shortName") or "").strip() or None
    except BudgetExhausted:
        raise
    except Exception:  # noqa: BLE001 - the name is cosmetic
        return None
    return None


def _locs(item: dict) -> list[str]:
    out = []
    for loc in item.get("jobLocations") or []:
        city = (loc.get("city") or "").strip(" .")
        name = (loc.get("name") or "").strip(" .")
        country = (loc.get("countryName") or "").strip(" .")
        parts = [p for p in (city or name, country) if p]
        if name and city and name.lower() != city.lower() and not name.lower().startswith(city.lower()):
            parts.insert(0, name)
        s = ", ".join(dict.fromkeys(parts))
        if s:
            out.append(s)
    return list(dict.fromkeys(out))


def _country(item: dict) -> str | None:
    codes = {(x.get("countryCode") or "").upper() for x in item.get("jobLocations") or [] if x.get("countryCode")}
    if "IN" in codes:
        return "IN"
    return next(iter(codes)) if len(codes) == 1 else None


def _loc_fields(item: dict) -> tuple[str | None, str | None, str | None]:
    locs = _locs(item)
    primary = next((x for x in locs if normalize_location(x)["location_tier"] in (1, 2, 3)),
                   locs[0] if locs else None)
    extra = "; ".join(x for x in locs if x != primary) or None
    return primary, _country(item), extra


def parse_experience_field(s) -> tuple[float | None, float | None]:
    s = str(s or "")
    m = _EXP_RANGE.search(s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (lo, hi) if lo <= hi <= 40 else (None, None)
    m = _EXP_MIN.search(s)
    if m and float(m.group(1)) <= 40:
        return float(m.group(1)), None
    return None, None


def parse_job(board: str, item: dict, company: str | None = None) -> Job:
    host, slug = host_for(board), slug_of(board)
    location, country, extra = _loc_fields(item)
    url = f"https://{host}/careers/jobdetails/{item.get('id')}"
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{item.get('id')}",
        title=(item.get("title") or "").strip(),
        company=company or pretty_company(slug),
        source_url=url,
        apply_url=url,
        ats_type="keka",
        ats_slug=slug,
        location=location,
        country=country,
        posted_at=item.get("publishedOn"),
        jd_text=item.get("description") or "",
        department=item.get("departmentName") or None,
        raw={"id": item.get("id"), "jobNumber": item.get("jobNumber")},
    )
    job.exp_min, job.exp_max = parse_experience_field(item.get("experience"))
    sr = item.get("salaryRange") or {}
    salary_text = None
    if sr.get("minimum") or sr.get("maximum"):
        job.salary_min = float(sr["minimum"]) if sr.get("minimum") else None
        job.salary_max = float(sr["maximum"]) if sr.get("maximum") else None
        job.salary_currency = sr.get("currency") or None
        job.salary_period = _PERIOD.get(sr.get("salaryPeriod"))
        if job.salary_period is None:     # unknown period code: let the text parser decide
            job.salary_min = job.salary_max = None
            salary_text = item.get("salaryRangeFormat") or None
    return finalize(job, jd_is_html=True, salary_text=salary_text, location_extra=extra)


def fetch_board(board: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """One list call (JD included) + one company-name call when anything survives the filters."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    kept: list[dict] = []
    for item in list_active(board, ctx, calls):
        if limit and len(kept) >= limit:
            break
        if not title_matches(item.get("title") or "", ctx.title_include, ctx.title_exclude):
            continue
        location, country, extra = _loc_fields(item)
        if not keep_location(location, country, extra=extra):
            continue
        kept.append(item)
    if not kept:
        return []
    try:
        company = company_name(board, ctx, calls)
    except BudgetExhausted as e:
        note_error(result, e)
        company = None
    return [parse_job(board, item, company) for item in kept]


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "keka", ctx, fetch_board)
