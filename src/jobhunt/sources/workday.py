"""Workday CXS job search — DISCOVERY ONLY (channel ends up manual_account_portal: Workday needs an account).

Watchlist key: the full tenant/site URL, e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
(a locale segment such as /en-US/ is ignored).

Search: POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
        {"appliedFacets": {...}, "limit": 20, "offset": n, "searchText": "<query>"}
        limit must be <= 20 (50 -> 400); ``total`` is reported on page 1 and capped around 2,000.
        Response: {total, jobPostings[{title, externalPath, locationsText, postedOn, bulletFields}], facets}
Detail: GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}
        -> {jobPostingInfo {jobDescription, location, additionalLocations, startDate, timeType, jobReqId,
           country {descriptor}, externalUrl, remoteType?}, hiringOrganization}
Apply:  https://{host}/en-US/{site}{externalPath}
Country: the first response's facets are searched for a location value named after your home country, e.g. "India" (locationCountry /
        locationHierarchy1 / ...); when found, every request for that board applies it, so only home-country
        postings are paged. A local location filter runs as well.
Errors: unknown site -> 404 {"errorCode":"S21"}; unknown tenant -> 422; Cloudflare-protected tenants -> 403.
Paging: ``ctx.budgets["workday_pages"]`` pages per query (default 2), ``ctx.budgets["workday"]`` total calls.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..models import Job
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .workable import BudgetExhausted, Calls, keep_location, note_error, pretty_company, run_boards

NAME = "workday"
PAGE = 20
MAX_TOTAL = 2000
DEFAULT_PAGES = 2
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_N_LOCS = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)


def parse_board_url(url: str) -> tuple[str, str, str]:
    """-> (host, tenant, site). Accepts hosted URLs (with or without locale) and cxs URLs."""
    p = urlparse((url or "").strip())
    host = (p.hostname or "").lower()
    if not host.endswith(("myworkdayjobs.com", "myworkdaysite.com")):
        raise SourceError(f"{NAME}:{url}: not a Workday careers URL")
    segs = [s for s in p.path.split("/") if s]
    if len(segs) >= 4 and segs[0] == "wday" and segs[1] == "cxs":
        return host, segs[2], segs[3]
    segs = [s for s in segs if not _LOCALE.match(s)]
    if not segs:
        raise SourceError(f"{NAME}:{url}: missing site segment (expected https://<tenant>.wdN.myworkdayjobs.com/<site>)")
    return host, host.split(".")[0], segs[0]


def cxs_base(url: str) -> str:
    host, tenant, site = parse_board_url(url)
    return f"https://{host}/wday/cxs/{tenant}/{site}"


def public_url(url: str, external_path: str) -> str:
    host, _, site = parse_board_url(url)
    return f"https://{host}/en-US/{site}{external_path}"


def find_india_facet(facets: list | None, country: str | None = None) -> tuple[str, str] | None:
    """(facetParameter, id) of a location facet value named after your home country (e.g. "India"),
    searching nested facet groups. The name is historical; the country comes from the profile."""
    if country is None:
        from ..geo import current
        country = current().home_name
    want = country.strip().lower()
    def walk(items, parent):
        for f in items or []:
            if not isinstance(f, dict):
                continue
            param = f.get("facetParameter") or parent
            for v in f.get("values") or []:
                if not isinstance(v, dict):
                    continue
                if "values" in v:
                    hit = walk([v], param)
                    if hit:
                        return hit
                elif (v.get("descriptor") or "").strip().lower() == want and v.get("id") and param \
                        and "loc" in param.lower() or (param or "").lower().endswith("country") \
                        and (v.get("descriptor") or "").strip().lower() == want and v.get("id"):
                    return param, v["id"]
        return None
    return walk(facets, None)


def search_page(board: str, ctx: SearchContext, *, query: str = "", offset: int = 0,
                facets: dict | None = None, calls: Calls | None = None) -> dict:
    url = f"{cxs_base(board)}/jobs"
    body = {"appliedFacets": facets or {}, "limit": PAGE, "offset": offset, "searchText": query or ""}
    kw = {"json": body, "headers": {"Accept": "application/json", "Content-Type": "application/json"}}
    r = calls.post(url, **kw) if calls else ctx.http.post(url, **kw)
    if r.status_code == 404:
        raise SourceError(f"{NAME}:{board}: not_found (404 {r.text[:120]})")
    try:
        data = r.json()
    except ValueError as e:
        raise SourceError(f"{NAME}:{board}: non-JSON response (HTTP {r.status_code})") from e
    if not isinstance(data, dict) or not isinstance(data.get("jobPostings", []), list):
        raise SourceError(f"{NAME}:{board}: unexpected payload")
    return data


def fetch_detail(board: str, external_path: str, ctx: SearchContext, calls: Calls | None = None) -> dict | None:
    url = f"{cxs_base(board)}{external_path}"
    data = calls.get_json(url) if calls else ctx.http.get_json(url)
    if data is None:
        return None
    return data.get("jobPostingInfo") or {}


def _path_location(external_path: str) -> str:
    """'/job/India-Bengaluru/Title_JR1' -> 'India Bengaluru'."""
    segs = [s for s in (external_path or "").split("/") if s]
    return segs[1].replace("-", " ") if len(segs) >= 3 and segs[0] == "job" else ""


def _posted(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"^\s*posted\s+", "", s, flags=re.I).strip() or None


def posting_location_ok(posting: dict) -> bool:
    path_loc = _path_location(posting.get("externalPath") or "")
    text = posting.get("locationsText") or ""
    extra = None if _N_LOCS.match(text) else text
    return keep_location(path_loc or extra or None, extra=extra if path_loc else None)


def parse_job(board: str, posting: dict, info: dict | None = None) -> Job:
    host, tenant, site = parse_board_url(board)
    d = info or {}
    path = posting.get("externalPath") or ""
    url = public_url(board, path)
    loc_text = posting.get("locationsText") or ""
    location = d.get("location") or (None if _N_LOCS.match(loc_text) else loc_text) or _path_location(path) or None
    extras = [x for x in d.get("additionalLocations") or [] if x]
    if d.get("remoteType"):
        extras.append(str(d["remoteType"]))
    req = d.get("jobReqId") or next(iter(posting.get("bulletFields") or []), None) or path
    country = ((d.get("jobRequisitionLocation") or {}).get("country") or {}).get("alpha2Code") \
        or (d.get("country") or {}).get("descriptor")
    job = Job(
        source=NAME,
        source_job_id=f"{tenant}/{site}:{req}",
        title=(posting.get("title") or d.get("title") or "").strip(),
        company=pretty_company(tenant),
        source_url=url,
        apply_url=url,
        ats_type="workday",
        ats_slug=f"{tenant}/{site}",
        location=location,
        country=country or None,
        posted_at=d.get("startDate") or _posted(posting.get("postedOn")),
        jd_text=d.get("jobDescription") or "",
        employment_type=d.get("timeType") or None,
        raw={"externalPath": path, "jobReqId": req, "host": host},
    )
    return finalize(job, jd_is_html=True, location_extra="; ".join(extras) or None)


def _pages_per_query(ctx: SearchContext) -> int:
    try:
        return max(1, int((ctx.budgets or {}).get("workday_pages", DEFAULT_PAGES)))
    except (TypeError, ValueError):
        return DEFAULT_PAGES


def fetch_board(board: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """Search one Workday tenant/site for each ctx.queries entry (or "" = all), home-country facet when available.

    Title + location filters run on the listing; details only for kept postings.
    """
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    parse_board_url(board)  # validate early
    queries = [q for q in (ctx.queries or []) if q is not None] or [""]
    facets: dict = {}
    facet_checked = False
    seen: set[str] = set()
    out: list[Job] = []
    try:
        for q in queries:
            offset, pages, total = 0, 0, None
            while pages < _pages_per_query(ctx):
                if limit and len(out) >= limit:
                    return out
                data = search_page(board, ctx, query=q, offset=offset, facets=facets, calls=calls)
                if not facet_checked:
                    facet_checked = True
                    hit = find_india_facet(data.get("facets"))
                    if hit:
                        facets = {hit[0]: [hit[1]]}
                        continue            # redo this page restricted to the home country
                if total is None:
                    total = min(int(data.get("total") or 0), MAX_TOTAL)
                postings = data.get("jobPostings") or []
                for p in postings:
                    path = p.get("externalPath") or ""
                    if not path or path in seen:
                        continue
                    seen.add(path)
                    if not title_matches(p.get("title") or "", ctx.title_include, ctx.title_exclude):
                        continue
                    if not facets and not posting_location_ok(p):
                        continue
                    if limit and len(out) >= limit:
                        return out
                    info: dict | None = {}
                    try:
                        info = fetch_detail(board, path, ctx, calls)
                    except BudgetExhausted:
                        raise
                    except Exception as e:  # noqa: BLE001
                        result.errors.append(f"{NAME}:{board}:{path}: detail failed: {type(e).__name__}: {e}")
                        info = {}
                    if info is None:        # gone
                        continue
                    job = parse_job(board, p, info)
                    if info and not keep_location(job.location, job.country) \
                            and not facets:
                        continue
                    out.append(job)
                pages += 1
                offset += PAGE
                if not postings or offset >= (total or 0):
                    break
    except BudgetExhausted as e:
        note_error(result, e)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        why = {422: "unknown tenant", 403: "blocked (Cloudflare?)"}.get(code, "http error")
        if out:
            result.errors.append(f"{NAME}:{board}: {why} ({code})")
        else:
            raise SourceError(f"{NAME}:{board}: {why} ({code})") from e
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "workday", ctx, fetch_board)
