"""Lever Postings API (Tier A2).

GET https://api.lever.co/v0/postings/{site}?mode=json      (global host)
GET https://api.eu.lever.co/v0/postings/{site}?mode=json   (EU host; tried on 404 before declaring a site dead)
Bad site -> 404 {"ok":false}; [] -> a valid board with no openings. Full descriptions come in the list,
so no detail calls are needed.
"""
from __future__ import annotations

from ..models import Job, RemoteType
from ..sanitize import html_to_text
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .greenhouse import CallCounter, location_ok, pretty_company, run_boards

NAME = "lever"
HOSTS = ("https://api.lever.co", "https://api.eu.lever.co")
_WORKPLACE = {"remote": RemoteType.REMOTE, "hybrid": RemoteType.HYBRID, "onsite": RemoteType.ONSITE,
              "on-site": RemoteType.ONSITE}
_PERIOD = {"per-year-salary": "year", "per-month-salary": "month", "per-hour-wage": "hour",
           "per-week-salary": None, "per-day-wage": None, "one-time": None}


def list_postings(site: str, ctx: SearchContext, counter: CallCounter | None = None) -> tuple[list[dict], str]:
    """-> (postings, host). Tries the global then the EU host; raises SourceError('not_found') if both 404."""
    for host in HOSTS:
        url = f"{host}/v0/postings/{site}"
        params = {"mode": "json"}
        data = counter.get_json(url, params=params) if counter else ctx.http.get_json(url, params=params)
        if data is None:
            continue
        if isinstance(data, dict) and data.get("ok") is False:
            continue
        if not isinstance(data, list):
            raise SourceError(f"{NAME}:{site}: unexpected payload")
        return data, host
    raise SourceError(f"{NAME}:{site}: not_found (404 on api.lever.co and api.eu.lever.co)")


def _locations(p: dict) -> list[str]:
    cats = p.get("categories") or {}
    locs = [x for x in (cats.get("allLocations") or []) if x]
    if cats.get("location") and cats["location"] not in locs:
        locs.insert(0, cats["location"])
    return locs


def _jd_html(p: dict) -> str:
    parts = [p.get("description")
             or "\n".join(x for x in (p.get("opening"), p.get("descriptionBody")) if x)]
    for lst in p.get("lists") or []:
        if lst.get("text"):
            parts.append(f"<h3>{lst['text']}</h3>")
        content = lst.get("content") or ""
        parts.append(content if content.lstrip().startswith(("<ul", "<ol")) else f"<ul>{content}</ul>")
    parts.append(p.get("additional") or "")
    return "\n".join(x for x in parts if x)


def _salary(job: Job, sr: dict | None) -> None:
    if not sr:
        return
    lo, hi = sr.get("min"), sr.get("max")
    if lo is None and hi is None:
        return
    job.salary_min = float(lo) if lo is not None else None
    job.salary_max = float(hi) if hi is not None else None
    job.salary_currency = sr.get("currency") or None
    job.salary_period = _PERIOD.get(sr.get("interval") or "", "year")


def parse_posting(site: str, p: dict) -> Job:
    cats = p.get("categories") or {}
    locs = _locations(p)
    wp = (p.get("workplaceType") or "").lower()
    job = Job(
        source=NAME,
        source_job_id=f"{site}:{p.get('id')}",
        title=(p.get("text") or "").strip(),
        company=pretty_company(site),
        source_url=p.get("hostedUrl"),
        apply_url=p.get("applyUrl") or (f"{p['hostedUrl']}/apply" if p.get("hostedUrl") else None),
        ats_type="lever",
        ats_slug=site,
        location="; ".join(locs) or None,
        country=(p.get("country") or None),
        remote_type=_WORKPLACE.get(wp, RemoteType.UNKNOWN),
        posted_at=p.get("createdAt"),
        jd_text=_jd_html(p),
        department=cats.get("team") or cats.get("department"),
        employment_type=cats.get("commitment"),
        raw={"id": p.get("id")},
    )
    _salary(job, p.get("salaryRange"))
    if job.country:
        job.country = job.country.upper()
    salary_text = None
    if job.salary_min is None and p.get("salaryDescriptionPlain"):
        salary_text = p["salaryDescriptionPlain"]
    elif job.salary_min is None and p.get("salaryDescription"):
        salary_text = html_to_text(p["salaryDescription"])
    return finalize(job, jd_is_html=True, salary_text=salary_text, is_remote=(wp == "remote") or None)


def fetch_board(site: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """All postings of a Lever site filtered by title and home-country/remote location."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    counter = CallCounter(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    postings, _host = list_postings(site, ctx, counter)
    out: list[Job] = []
    for p in postings:
        if limit and len(out) >= limit:
            break
        if not title_matches(p.get("text") or "", ctx.title_include, ctx.title_exclude):
            continue
        wp = (p.get("workplaceType") or "").lower()
        if not location_ok("; ".join(_locations(p)) or None, p.get("country"), is_remote=(wp == "remote") or None):
            continue
        try:
            out.append(parse_posting(site, p))
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{site}:{p.get('id')}: parse failed: {type(e).__name__}: {e}")
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "lever", ctx, fetch_board)
