"""Zoho Recruit career sites (India-heavy ATS; channel auto_probe via the classifier).

Page:   GET https://{slug}.zohorecruit.com/jobs/Careers   (or .in / .eu data centres, or a custom page name)
        The server-rendered page (~1.7-2.2 MB) embeds every published job as HTML-escaped JSON in
        ``<input type="hidden" value="[...]" id="jobs">`` and the org info in ``<input ... id="meta">``
        (``meta.org_info.company_name``, ``meta.list_url``, ``meta.page_name``).
        Job keys: id, Posting_Title, Job_Opening_Name, City, State, Country, Remote_Job, Job_Description,
        Job_Type, Work_Experience, Industry, Currency, Salary (optional), Date_Opened, Publish.
Apply:  https://{host}/jobs/{page_name}/{id}/{Title-Slug}?source=CareerSite
Bad slug: 200 with ``<title>Page does not exist</title>`` (and no ``id="jobs"`` input).
The ``/jobs/Careers/rss`` feed is often disabled ("Oops! It seems that the joblist has been removed."), so
the page itself is parsed.
"""
from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse

from ..models import Job, RemoteType
from ..normalize import parse_experience
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .workable import Calls, keep_location, pretty_company, run_boards

NAME = "zohorecruit"
_INPUT = r'<input[^>]*?value="([^"]*)"[^>]*?id="{id}"'
_INPUT2 = r'<input[^>]*?id="{id}"[^>]*?value="([^"]*)"'
_NOT_FOUND = re.compile(r"<title>\s*Page does not exist", re.I)
_DOMAINS = ("com", "in", "eu")


def page_urls(board: str) -> list[str]:
    """Candidate career-page URLs for a watchlist entry.

    'agilisium' -> .com then .in ; 'agilisium.zohorecruit.in' -> that host ; a full URL -> as given
    (``/jobs/Careers`` appended when the path is empty).
    """
    b = (board or "").strip()
    if "://" in b:
        p = urlparse(b)
        path = p.path.rstrip("/") or "/jobs/Careers"
        return [f"https://{p.hostname}{path}"]
    b = b.strip("/")
    if ".zohorecruit." in b:
        host, _, path = b.partition("/")
        return [f"https://{host}/{path or 'jobs/Careers'}"]
    return [f"https://{b}.zohorecruit.{d}/jobs/Careers" for d in _DOMAINS[:2]]


def _hidden(page: str, id_: str):
    m = re.search(_INPUT.format(id=id_), page) or re.search(_INPUT2.format(id=id_), page)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None


def parse_page(page: str) -> tuple[list[dict] | None, dict]:
    """(jobs or None when the page has no job list, meta dict)."""
    jobs = _hidden(page, "jobs")
    meta = _hidden(page, "meta") or {}
    return (jobs if isinstance(jobs, list) else None), (meta if isinstance(meta, dict) else {})


def fetch_page(board: str, ctx: SearchContext, calls: Calls | None = None) -> tuple[str, list[dict], dict]:
    """(page_url, jobs, meta). Raises SourceError('not_found') when no candidate URL has a job list."""
    tried = []
    for url in page_urls(board):
        r = calls.get(url) if calls else ctx.http.get(url)
        tried.append(f"{url} -> {r.status_code}")
        if r.status_code != 200 or _NOT_FOUND.search(r.text[:5000]):
            continue
        jobs, meta = parse_page(r.text)
        if jobs is None:
            tried[-1] += " (no embedded job list)"
            continue
        return str(r.url), jobs, meta
    raise SourceError(f"{NAME}:{board}: not_found ({'; '.join(tried)})")


def _str(v) -> str:
    return "" if v is None or str(v).strip().lower() in ("none", "null") else str(v).strip()


def _location(item: dict) -> tuple[str | None, str | None]:
    parts = [_str(item.get("City")), _str(item.get("State")), _str(item.get("Country"))]
    loc = ", ".join(dict.fromkeys(p for p in parts if p)) or None
    return loc, (_str(item.get("Country")) or None)


def _is_remote(item: dict) -> bool | None:
    v = item.get("Remote_Job")
    return True if v is True or str(v).lower() == "true" else None


def detail_url(page_url: str, meta: dict, item: dict) -> str:
    p = urlparse(meta.get("list_url") or page_url)
    base = f"https://{p.hostname}{p.path.rstrip('/')}"
    title = _str(item.get("Posting_Title") or item.get("Job_Opening_Name"))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "job"
    return f"{base}/{item.get('id')}/{slug}?source=CareerSite"


def parse_job(board: str, page_url: str, meta: dict, item: dict) -> Job:
    host = urlparse(page_url).hostname or ""
    slug = host.split(".")[0]
    location, country = _location(item)
    remote = _is_remote(item)
    company = _str((meta.get("org_info") or {}).get("company_name")) or pretty_company(slug)
    url = detail_url(page_url, meta, item)
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{item.get('id')}",
        title=_str(item.get("Posting_Title") or item.get("Job_Opening_Name")),
        company=company,
        source_url=url,
        apply_url=url,
        ats_type="zohorecruit",
        ats_slug=slug,
        location=location,
        country="IN" if country and country.lower() == "india" else (country or None),
        remote_type=RemoteType.REMOTE if remote else RemoteType.UNKNOWN,
        posted_at=_str(item.get("Date_Opened")) or None,
        jd_text=_str(item.get("Job_Description")),
        department=_str(item.get("Industry")) or None,
        employment_type=_str(item.get("Job_Type")) or None,
        raw={"id": item.get("id")},
    )
    exp = _str(item.get("Work_Experience"))
    if exp:
        job.exp_min, job.exp_max = parse_experience(exp if re.search(r"year|yr|fresher", exp, re.I)
                                                    else f"{exp} years")
    sal = _str(item.get("Salary"))
    salary_text = f"{_str(item.get('Currency'))} {sal}".strip() if sal else None
    return finalize(job, jd_is_html=True, salary_text=salary_text, is_remote=remote)


def fetch_board(board: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """One page fetch per board (JD embedded); title + India/remote filter."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    page_url, jobs, meta = fetch_page(board, ctx, calls)
    out: list[Job] = []
    for item in jobs:
        if limit and len(out) >= limit:
            break
        if str(item.get("Publish", True)).lower() == "false":
            continue
        title = _str(item.get("Posting_Title") or item.get("Job_Opening_Name"))
        if not title_matches(title, ctx.title_include, ctx.title_exclude):
            continue
        location, country = _location(item)
        if not keep_location(location, country, is_remote=_is_remote(item)):
            continue
        out.append(parse_job(board, page_url, meta, item))
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "zohorecruit", ctx, fetch_board)

