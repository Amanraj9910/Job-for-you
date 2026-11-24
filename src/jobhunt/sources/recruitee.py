"""Recruitee (Tier A-minus, public careers-site API).

List:   GET https://{slug}.recruitee.com/api/offers/   -> {"offers": [...]} with description + requirements HTML,
        location / city / country_code / remote / hybrid / on_site, salary {min,max,period,currency}
Detail: GET https://{slug}.recruitee.com/api/offers/{offer_slug}  (not needed: the list carries the JD)
A bad slug returns 404. ``careers_url`` may point to a custom domain (careers.example.com); the apply URL we
store is the recruitee-hosted ``https://{slug}.recruitee.com/o/{offer_slug}`` (it 302s to the custom domain)
so the channel classifier recognises the ATS.
"""
from __future__ import annotations

from ..models import Job, RemoteType
from ..normalize import normalize_location
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .workable import Calls, keep_location, pretty_company, run_boards

NAME = "recruitee"

_PERIOD = {"year": "year", "yearly": "year", "annual": "year", "month": "month", "monthly": "month",
           "hour": "hour", "hourly": "hour"}


def api_url(slug: str) -> str:
    return f"https://{slug}.recruitee.com/api/offers/"


def list_offers(slug: str, ctx: SearchContext, calls: Calls | None = None) -> list[dict]:
    """Raw offers for a board. Raises SourceError('not_found') on a bad slug."""
    url = api_url(slug)
    data = calls.get_json(url) if calls else ctx.http.get_json(url)
    if data is None:
        raise SourceError(f"{NAME}:{slug}: not_found (404)")
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        raise SourceError(f"{NAME}:{slug}: unexpected payload")
    return data["offers"]


def _locations(o: dict) -> list[str]:
    out: list[str] = []
    for loc in o.get("locations") or []:
        parts = [loc.get("city"), loc.get("state"), loc.get("country")]
        s = ", ".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))
        if s:
            out.append(s)
    if not out and o.get("location"):
        out.append(o["location"])
    return list(dict.fromkeys(out))


def _location_fields(o: dict) -> tuple[str | None, str | None, str | None]:
    """(location, country_code, extra) for the filter and the Job."""
    locs = _locations(o)
    cc = (o.get("country_code") or "").upper() or None
    primary = next((x for x in locs if normalize_location(x)["location_tier"] in (1, 2, 3)), locs[0] if locs
                   else None)
    extra = "; ".join(x for x in locs if x != primary) or None
    if o.get("remote") and primary and "remote" not in primary.lower():
        primary = f"Remote - {primary}"
    return primary, cc, extra


def parse_offer(slug: str, o: dict) -> Job:
    location, cc, extra = _location_fields(o)
    rtype = RemoteType.REMOTE if o.get("remote") else RemoteType.HYBRID if o.get("hybrid") else \
        RemoteType.ONSITE if o.get("on_site") else RemoteType.UNKNOWN
    apply_url = f"https://{slug}.recruitee.com/o/{o.get('slug')}" if o.get("slug") else o.get("careers_url")
    jd = "\n".join(x for x in (o.get("description"), o.get("requirements")) if x)
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{o.get('id')}",
        title=(o.get("title") or "").strip(),
        company=(o.get("company_name") or pretty_company(slug)).strip(),
        source_url=o.get("careers_url") or apply_url,
        apply_url=apply_url,
        ats_type="recruitee",
        ats_slug=slug,
        location=location,
        country=cc,
        remote_type=rtype,
        posted_at=o.get("published_at") or o.get("created_at"),
        jd_text=jd,
        department=o.get("department") or None,
        employment_type=o.get("employment_type_code") or None,
        raw={"id": o.get("id"), "slug": o.get("slug"), "guid": o.get("guid")},
    )
    sal = o.get("salary") or {}
    salary_text = None
    if isinstance(sal, dict) and (sal.get("min") or sal.get("max")):
        try:
            job.salary_min = float(sal["min"]) if sal.get("min") not in (None, "") else None
            job.salary_max = float(sal["max"]) if sal.get("max") not in (None, "") else None
        except (TypeError, ValueError):
            job.salary_min = job.salary_max = None
            salary_text = f"{sal.get('currency') or ''} {sal.get('min') or ''} - {sal.get('max') or ''}"
        job.salary_currency = (sal.get("currency") or None)
        job.salary_period = _PERIOD.get(str(sal.get("period") or "").lower(), "year")
    return finalize(job, jd_is_html=True, salary_text=salary_text, is_remote=bool(o.get("remote")) or None,
                    location_extra=extra)


def fetch_board(slug: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """One call per board (the list already carries the JD); title + home-country/remote filter."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    out: list[Job] = []
    for o in list_offers(slug, ctx, calls):
        if limit and len(out) >= limit:
            break
        if (o.get("status") or "published") != "published":
            continue
        if not title_matches(o.get("title") or "", ctx.title_include, ctx.title_exclude):
            continue
        location, cc, extra = _location_fields(o)
        if not keep_location(location, cc, is_remote=bool(o.get("remote")) or None, extra=extra):
            continue
        out.append(parse_offer(slug, o))
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "recruitee", ctx, fetch_board)
