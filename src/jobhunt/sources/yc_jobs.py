"""Y Combinator job board (India + remote listing pages for India-based profiles, remote page otherwise).

Listing pages (server-rendered; the job list is JSON in the ``data-page`` attribute of the React root, component
``WaasJobListingsPage``, ``props.jobPostings``, ~24-50 postings per page, no pagination):
  /jobs/location/india, /jobs/role/software-engineer/india, /jobs/location/remote
(``/jobs/location/bangalore`` is not a real filter — it falls back to the generic list — so it is not used.)
Detail page ``/companies/<slug>/jobs/<id>-<slug>`` (component ``WaasShowJobPage``) adds ``job.description``
(markdown) and ``company.website``. Applying goes through a YC / Work at a Startup account, so jobs classify as
``manual_other``; the company website and any ATS link found in the description are kept in ``job.raw`` for
watchlist discovery.
"""
from __future__ import annotations

import html as htmlmod
import json
import re

from bs4 import BeautifulSoup

from ..classify import detect_ats
from ..models import Job
from ..normalize import is_home, normalize_location, parse_experience, parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches
from .himalayas import geo_ok, safe_location

NAME = "yc_jobs"
BASE = "https://www.ycombinator.com"
LIST_PATHS = ["/jobs/location/india", "/jobs/role/software-engineer/india", "/jobs/location/remote"]
REMOTE_PATHS = ["/jobs/location/remote"]


def list_paths() -> list[str]:
    """India pages only make sense for India-based profiles; everyone gets the remote page."""
    from ..geo import current
    return LIST_PATHS if current().home_cc == "IN" else REMOTE_PATHS
DEFAULT_DETAIL_BUDGET = 25

_URL = re.compile(r"https?://[^\s)\]>\"']+")


def page_data(page_html: str) -> dict:
    """The decoded ``data-page`` JSON ({component, props}) of a YC page, or {}."""
    soup = BeautifulSoup(page_html or "", "html.parser")
    el = soup.find(attrs={"data-page": True})
    if el is None:
        return {}
    try:
        return json.loads(el["data-page"])
    except (ValueError, TypeError):
        try:
            return json.loads(htmlmod.unescape(el["data-page"]))
        except (ValueError, TypeError):
            return {}


def fetch_listing(path: str, ctx: SearchContext) -> list[dict]:
    """Job postings of one listing page ([] on 404 or when no data-page JSON is present)."""
    r = ctx.http.get(BASE + path if path.startswith("/") else path)
    if r.status_code == 404:
        return []
    return (page_data(r.text).get("props") or {}).get("jobPostings") or []


def fetch_detail(job_path: str, ctx: SearchContext) -> dict:
    """{'job': {...description...}, 'company': {...website...}} for one posting, or {}."""
    r = ctx.http.get(BASE + job_path if job_path.startswith("/") else job_path)
    if r.status_code == 404:
        return {}
    props = page_data(r.text).get("props") or {}
    return {"job": props.get("job") or {}, "company": props.get("company") or {}}


def _posted(created: str | None) -> str | None:
    if not created:
        return None
    c = created.strip().lower().replace("about ", "").replace("over ", "").replace("almost ", "")
    c = re.sub(r"^(an?|one)\s+", "1 ", c)
    return c if c.endswith("ago") else f"{c} ago"


def _prefilter(p: dict, ctx: SearchContext) -> bool:
    """Cheap title + home-country/remote check on a listing posting, before any detail fetch."""
    title = p.get("title") or ""
    if not title_matches(title, ctx.title_include, ctx.title_exclude):
        return False
    loc = safe_location(p.get("location"))
    extra = title if is_home(title) else None
    n = normalize_location(loc, is_remote=None, extra_text=extra)
    probe = Job(source=NAME, source_job_id="x", title=title, company="x", location=n["location"],
                remote_type=n["remote_type"], remote_scope=n["remote_scope"], location_tier=n["location_tier"])
    return geo_ok(probe)


def build_job(p: dict, detail: dict | None = None) -> Job:
    detail = detail or {}
    dj, co = detail.get("job") or {}, detail.get("company") or {}
    url = BASE + (p.get("url") or dj.get("url") or "")
    title = (p.get("title") or dj.get("title") or "").strip()
    desc = dj.get("description") or ""
    head = [x for x in (p.get("companyOneLiner"), p.get("type"), p.get("visa") and f"Visa: {p['visa']}") if x]
    jd = "\n".join(head) + ("\n\n" + desc if desc else "")
    raw: dict = {"id": p.get("id"), "company_slug": (p.get("companyUrl") or "").rsplit("/", 1)[-1] or None}
    if co.get("website"):
        raw["company_website"] = co["website"]
    for u in _URL.findall(desc):
        ats, _ = detect_ats(u)
        if ats:
            raw["ats_url"] = u
            break
    job = Job(source=NAME, source_job_id=str(p.get("id") or dj.get("id")), title=title,
              company=(p.get("companyName") or co.get("name") or "").strip(), source_url=url, apply_url=url,
              location=safe_location(p.get("location")) or None, posted_at=parse_posted_at(_posted(p.get("createdAt"))), jd_text=jd,
              department=p.get("prettyRole"), employment_type=p.get("type"), raw=raw)
    lo, hi = parse_experience(p.get("minExperience") or "")
    if lo is not None:
        job.exp_min, job.exp_max = lo, hi
    extra = title if is_home(title) else None
    return finalize(job, jd_is_html=False, salary_text=p.get("salaryRange") or None, location_extra=extra)


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(NAME, [])
    paths = list(ctx.budgets.get("yc_jobs_paths") or list_paths())
    detail_budget = int(ctx.budgets.get(NAME, DEFAULT_DETAIL_BUDGET))
    postings: dict[str, dict] = {}
    for path in paths:
        try:
            res.calls += 1
            for p in fetch_listing(path, ctx):
                if p.get("id") is not None:
                    postings.setdefault(str(p["id"]), p)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{NAME} {path}: {type(e).__name__}: {e}")
    if not postings and not res.errors:
        res.errors.append(f"{NAME}: no jobPostings found in data-page JSON (page layout changed?)")
    details = 0
    for p in postings.values():
        if len(res.jobs) >= ctx.limit:
            break
        try:
            if not _prefilter(p, ctx):
                continue
            detail = None
            if details < detail_budget and p.get("url"):
                details += 1
                res.calls += 1
                try:
                    detail = fetch_detail(p["url"], ctx)
                except Exception as e:  # noqa: BLE001
                    res.errors.append(f"{NAME} detail {p.get('url')}: {type(e).__name__}: {e}")
            job = build_job(p, detail)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{NAME} parse {p.get('id')}: {type(e).__name__}: {e}")
            continue
        if geo_ok(job):
            res.jobs.append(job)
    return res
