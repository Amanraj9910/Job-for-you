"""Ashby Posting API (Tier A3).

GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true
One call returns every listed job with ``descriptionHtml``/``descriptionPlain``, ``jobUrl``, ``applyUrl`` and
compensation, so no detail calls are needed. A bad board name returns 404. Responses can be 2-3 MB.
"""
from __future__ import annotations

from ..models import Job, RemoteType
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .greenhouse import CallCounter, location_ok, pretty_company, run_boards

NAME = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board"
_WORKPLACE = {"remote": RemoteType.REMOTE, "hybrid": RemoteType.HYBRID, "onsite": RemoteType.ONSITE,
              "on-site": RemoteType.ONSITE}
_INTERVAL = {"1 YEAR": "year", "1 MONTH": "month", "1 HOUR": "hour"}


def list_jobs(name: str, ctx: SearchContext, counter: CallCounter | None = None) -> list[dict]:
    """Raw job dicts for a board. Raises SourceError('not_found') on a bad name."""
    url = f"{API}/{name}"
    params = {"includeCompensation": "true"}
    data = counter.get_json(url, params=params) if counter else ctx.http.get_json(url, params=params)
    if data is None:
        raise SourceError(f"{NAME}:{name}: not_found (404)")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError(f"{NAME}:{name}: unexpected payload")
    return data["jobs"]


def _secondary(j: dict) -> list[str]:
    out = []
    for s in j.get("secondaryLocations") or []:
        loc = s.get("location") if isinstance(s, dict) else s
        if loc and isinstance(loc, str):
            out.append(loc.strip())
    return out


def _locations(j: dict) -> list[str]:
    locs: list[str] = []
    for x in [j.get("location")] + _secondary(j):
        if x and isinstance(x, str) and x.strip() and x.strip() not in locs:
            locs.append(x.strip())
    return locs


def _address_country(j: dict) -> str | None:
    pa = ((j.get("address") or {}).get("postalAddress") or {})
    c = (pa.get("addressCountry") or "").strip()
    return c or None


def _is_remote(j: dict) -> bool | None:
    if j.get("isRemote") is True or (j.get("workplaceType") or "").lower() == "remote":
        return True
    return None


def _salary(job: Job, comp: dict | None) -> str | None:
    """Set structured salary from ``summaryComponents``; return summary text for finalize() otherwise."""
    comp = comp or {}
    for c in comp.get("summaryComponents") or []:
        if (c.get("compensationType") or "").lower() != "salary":
            continue
        lo, hi = c.get("minValue"), c.get("maxValue")
        if lo is None and hi is None:
            continue
        job.salary_min = float(lo) if lo is not None else None
        job.salary_max = float(hi) if hi is not None else None
        job.salary_currency = c.get("currencyCode") or None
        job.salary_period = _INTERVAL.get((c.get("interval") or "").upper(), "year")
        return None
    return comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary") or None


def parse_job(name: str, j: dict) -> Job:
    locs = _locations(j)
    wp = (j.get("workplaceType") or "").lower()
    country = _address_country(j)
    url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{name}/{j.get('id')}"
    html = j.get("descriptionHtml") or ""
    job = Job(
        source=NAME,
        source_job_id=f"{name}:{j.get('id')}",
        title=(j.get("title") or "").strip(),
        company=pretty_company(name),
        source_url=url,
        apply_url=j.get("applyUrl") or f"{url}/application",
        ats_type="ashby",
        ats_slug=name,
        location="; ".join(locs) or None,
        country="IN" if (country or "").lower() == "india" else country,
        remote_type=_WORKPLACE.get(wp, RemoteType.UNKNOWN),
        posted_at=j.get("publishedAt"),
        jd_text=html or j.get("descriptionPlain") or "",
        department=j.get("department") or j.get("team"),
        employment_type=j.get("employmentType"),
        raw={"id": j.get("id")},
    )
    salary_text = _salary(job, j.get("compensation"))
    return finalize(job, jd_is_html=bool(html), salary_text=salary_text, is_remote=_is_remote(j))


def fetch_board(name: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """All listed jobs of an Ashby board filtered by title and home-country/remote location."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    counter = CallCounter(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    out: list[Job] = []
    for j in list_jobs(name, ctx, counter):
        if limit and len(out) >= limit:
            break
        if j.get("isListed") is False:
            continue
        if not title_matches(j.get("title") or "", ctx.title_include, ctx.title_exclude):
            continue
        if not location_ok("; ".join(_locations(j)) or None, _address_country(j), is_remote=_is_remote(j)):
            continue
        try:
            out.append(parse_job(name, j))
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{name}:{j.get('id')}: parse failed: {type(e).__name__}: {e}")
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "ashby", ctx, fetch_board)
