"""Breezy HR (Tier A-minus, public careers-site JSON).

List:   GET https://{slug}.breezy.hr/json  -> [{id, friendly_id, name, url, published_date, type{name},
           location {country{name,id}, state, city, name, is_remote, remote_details{value,label}}, locations[],
           department, salary (text), company{name}}]  (no description)
Detail: GET https://{slug}.breezy.hr/p/{friendly_id}  (HTML page; JD read from its schema.org JobPosting
           JSON-LD, falling back to ``div.description``)
Apply:  the posting ``url`` (https://{slug}.breezy.hr/p/{friendly_id})
Bad slug: 404 (HTML).
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..models import Job, RemoteType
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .workable import BudgetExhausted, Calls, keep_location, note_error, pretty_company, run_boards

NAME = "breezy"
_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def list_url(slug: str) -> str:
    return f"https://{slug}.breezy.hr/json"


def list_jobs(slug: str, ctx: SearchContext, calls: Calls | None = None) -> list[dict]:
    url = list_url(slug)
    data = calls.get_json(url) if calls else ctx.http.get_json(url)
    if data is None:
        raise SourceError(f"{NAME}:{slug}: not_found (404)")
    if not isinstance(data, list):
        raise SourceError(f"{NAME}:{slug}: unexpected payload")
    return data


def _one_loc(loc: dict) -> str:
    if not loc:
        return ""
    country = (loc.get("country") or {}).get("name") if isinstance(loc.get("country"), dict) else loc.get("country")
    state = (loc.get("state") or {}).get("name") if isinstance(loc.get("state"), dict) else loc.get("state")
    parts = [loc.get("city"), state, country]
    s = ", ".join(dict.fromkeys(p.strip() for p in parts if isinstance(p, str) and p.strip()))
    return s or (loc.get("name") or "")


def _loc_fields(item: dict) -> tuple[str | None, str | None, bool | None, str | None, RemoteType]:
    """(location, country_code, is_remote, extra, remote_type)."""
    main = item.get("location") or {}
    locs = [_one_loc(main)] + [_one_loc(x) for x in item.get("locations") or []]
    locs = [x for x in dict.fromkeys(locs) if x]
    country = main.get("country") if isinstance(main.get("country"), dict) else {}
    cc = (country or {}).get("id") or None
    rd = main.get("remote_details") or {}
    rval = (rd.get("value") or "").lower()
    if main.get("is_remote") or rval == "remote":
        rtype, remote = RemoteType.REMOTE, True
    elif rval == "hybrid":
        rtype, remote = RemoteType.HYBRID, None
    else:
        rtype, remote = RemoteType.UNKNOWN, None
    primary = locs[0] if locs else None
    extra = "; ".join(x for x in [*locs[1:], rd.get("label") or ""] if x) or None
    return primary, cc, remote, extra, rtype


def extract_description(page_html: str) -> str:
    """JD HTML from a Breezy posting page (JSON-LD JobPosting, else div.description)."""
    for m in _LD.finditer(page_html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") == "JobPosting" and d.get("description"):
                return d["description"]
    soup = BeautifulSoup(page_html or "", "html.parser")
    div = soup.find("div", class_="description")
    return div.decode_contents() if div else ""


def parse_job(slug: str, item: dict, jd_html: str = "") -> Job:
    location, cc, remote, extra, rtype = _loc_fields(item)
    url = item.get("url") or f"https://{slug}.breezy.hr/p/{item.get('friendly_id')}"
    company = ((item.get("company") or {}).get("name") or pretty_company(slug)).strip()
    typ = item.get("type") or {}
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{item.get('id')}",
        title=(item.get("name") or "").strip(),
        company=company,
        source_url=url,
        apply_url=url,
        ats_type="breezy",
        ats_slug=slug,
        location=location,
        country=cc,
        remote_type=rtype,
        posted_at=item.get("published_date"),
        jd_text=jd_html or "",
        department=item.get("department") or None,
        employment_type=(typ.get("id") or typ.get("name")) if isinstance(typ, dict) else None,
        raw={"id": item.get("id"), "friendly_id": item.get("friendly_id")},
    )
    return finalize(job, jd_is_html=True, salary_text=item.get("salary") or None, is_remote=remote,
                    location_extra=extra)


def fetch_board(slug: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """List a Breezy board, filter title + home-country/remote, then fetch posting pages for kept jobs only."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    out: list[Job] = []
    for item in list_jobs(slug, ctx, calls):
        if limit and len(out) >= limit:
            break
        if not title_matches(item.get("name") or "", ctx.title_include, ctx.title_exclude):
            continue
        location, cc, remote, extra, _ = _loc_fields(item)
        if not keep_location(location, cc, is_remote=remote, extra=extra):
            continue
        url = item.get("url") or f"https://{slug}.breezy.hr/p/{item.get('friendly_id')}"
        jd = ""
        try:
            r = calls.get(url, headers={"Accept": "text/html"})
            if r.status_code == 404:
                continue
            jd = extract_description(r.text)
        except BudgetExhausted as e:
            note_error(result, e)
            break
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{slug}:{item.get('id')}: detail failed: {type(e).__name__}: {e}")
        out.append(parse_job(slug, item, jd))
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "breezy", ctx, fetch_board)
