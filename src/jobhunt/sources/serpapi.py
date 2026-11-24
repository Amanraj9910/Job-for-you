"""SerpApi Google Jobs source (optional; needs SERPAPI_KEY).

Endpoint: ``GET https://serpapi.com/search.json?engine=google_jobs&q=..&location=..&gl=<home cc>&hl=en&api_key=..``
then ``&next_page_token=..`` (``start`` was discontinued). 10 results per call; budget
``ctx.budgets['serpapi']`` (default 8) calls per run.

Helpers:
* ``fetch_page(query, location, ctx, key, token=None)`` -> dict (raises on HTTP errors)
* ``pick_apply_url(options)`` -> best apply link (ATS > employer site > first)
* ``result_to_job(item)`` -> Job | None (finalized)
* ``parse_results(data)`` -> (jobs, next_page_token)

The API key is never put into error messages or job fields.
"""
from __future__ import annotations

import logging
import re

import httpx

from ..classify import AUTO_ATS, PROBE_ATS, board_kind, detect_ats
from ..config import env
from ..geo import current
from ..models import Job
from ..normalize import parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches
from .jobspy_src import is_relevant, spread_pairs

log = logging.getLogger(__name__)

NAME = "serpapi"
ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_BUDGET = 8
_REMOTE_LOC = re.compile(r"\bremote\b", re.IGNORECASE)
_NO_RESULTS = re.compile(r"hasn'?t returned any results", re.IGNORECASE)
_USD = re.compile(r"\bUS\s?\$", re.IGNORECASE)
_MONEY = re.compile(r"[₹$€£]|\b(inr|usd|lpa|lakhs?|a year|an hour|a month|per (year|month|hour))\b", re.IGNORECASE)


def _ats_rank(url: str) -> int | None:
    ats, _ = detect_ats(url)
    if not ats:
        return None
    return 0 if ats in AUTO_ATS else 1 if ats in PROBE_ATS else 2


def pick_apply_url(options: list[dict] | None) -> str | None:
    links = [o.get("link") for o in (options or []) if isinstance(o, dict) and o.get("link")]
    if not links:
        return None
    ranked = [(r, i) for i, u in enumerate(links) if (r := _ats_rank(u)) is not None]
    if ranked:
        return links[min(ranked)[1]]
    employer = [u for u in links if board_kind(u) is None]
    return employer[0] if employer else links[0]


def _salary_text(item: dict) -> str | None:
    de = item.get("detected_extensions") or {}
    text = None
    if de.get("salary"):
        text = str(de["salary"])
    else:
        for ext in item.get("extensions") or []:
            if isinstance(ext, str) and _MONEY.search(ext) and re.search(r"\d", ext):
                text = ext
                break
    # Google writes "US$80K–US$120K a year"; the "US" glued to the 2nd amount breaks the range parse
    return _USD.sub("$", text) if text else None


def result_to_job(item: dict) -> Job | None:
    title, company = item.get("title"), item.get("company_name")
    jid = item.get("job_id")
    if not title or not jid:
        return None
    de = item.get("detected_extensions") or {}
    opts = item.get("apply_options") or []
    links = [o["link"] for o in opts if isinstance(o, dict) and o.get("link")]
    jd = item.get("description") or ""
    for h in item.get("job_highlights") or []:
        if isinstance(h, dict) and h.get("items") and h.get("title"):
            jd += f"\n\n{h['title']}:\n" + "\n".join(f"- {x}" for x in h["items"] if isinstance(x, str))
    job = Job(
        source=NAME,
        source_job_id=str(jid),
        title=str(title),
        company=str(company or "Unknown"),
        source_url=item.get("share_link") or (links[0] if links else None),
        apply_url=pick_apply_url(opts),
        location=item.get("location"),
        posted_at=None,
        jd_text=jd,
        employment_type=de.get("schedule_type"),
        raw={"apply_options": links, "via": item.get("via")},
    )
    job.posted_at = parse_posted_at(de.get("posted_at"))
    remote = True if de.get("work_from_home") else None
    return finalize(job, jd_is_html=False, salary_text=_salary_text(item), is_remote=remote)


def parse_results(data: dict | None) -> tuple[list[Job], str | None]:
    if not data:
        return [], None
    jobs = []
    for item in data.get("jobs_results") or []:
        if not isinstance(item, dict):
            continue
        try:
            j = result_to_job(item)
        except Exception as e:  # noqa: BLE001 — one malformed result must not sink the page
            log.warning("serpapi: skipping result: %s", e)
            continue
        if j:
            jobs.append(j)
    token = (data.get("serpapi_pagination") or {}).get("next_page_token")
    return jobs, token


def _query_location(query: str, loc: str | None) -> tuple[str, str]:
    """City goes into q, location stays your home country (avoids SerpApi 'Unsupported location' errors)."""
    home = current().home_name
    if not loc or loc.strip().lower() == home.lower():
        return query, home
    if _REMOTE_LOC.search(loc):
        return f"{query} remote", home
    return f"{query} {loc}", home


def fetch_page(query: str, location: str | None, ctx: SearchContext, key: str,
               token: str | None = None) -> dict:
    """One SerpApi call. Raises on HTTP errors, non-JSON bodies and replies that are not a JSON object (incl. 404)."""
    q, where = _query_location(query, location)
    params = {"engine": "google_jobs", "q": q, "location": where, "gl": current().home_cc.lower(), "hl": "en", "api_key": key}
    if token:
        params["next_page_token"] = token
    data = ctx.http.get_json(ENDPOINT, params=params)
    if not isinstance(data, dict):
        raise TypeError(f"unexpected reply: {'HTTP 404' if data is None else type(data).__name__}")
    return data


def _err(e: Exception, key: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        msg = f"HTTP {e.response.status_code}"
        try:
            body = e.response.json()
            if isinstance(body, dict) and body.get("error"):
                msg += f": {body['error']}"
        except ValueError:  # body is not JSON: the status code alone will do
            pass
    else:
        msg = f"{type(e).__name__}: {e}"
    return msg.replace(key, "***")[:300] if key else msg[:300]


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(source=NAME, jobs=[])
    key = env("SERPAPI_KEY") or ""
    if not key:
        return res            # optional source: silently off without a key
    try:
        budget = int(ctx.budgets.get(NAME, DEFAULT_BUDGET))
        if not ctx.queries:
            res.errors.append("no queries")
            return res
        pairs = spread_pairs(ctx.queries, ctx.locations)
        pages_per_pair = max(1, budget // len(pairs))
        seen: set[str] = set()
        for q, loc in pairs:
            token, pages, got = None, 0, 0
            while res.calls < budget and pages < pages_per_pair and got < ctx.limit:
                res.calls += 1
                pages += 1
                try:
                    data = fetch_page(q, loc, ctx, key, token)
                except Exception as e:  # noqa: BLE001 — recorded per call, run continues
                    res.errors.append(f"q={q!r} loc={loc!r}: {_err(e, key)}")
                    break
                if data.get("error"):
                    if not _NO_RESULTS.search(str(data["error"])):
                        res.errors.append(f"q={q!r} loc={loc!r}: {str(data['error'])[:200]}")
                    break
                jobs, token = parse_results(data)
                for j in jobs:
                    if j.source_job_id in seen:
                        continue
                    seen.add(j.source_job_id)
                    if not title_matches(j.title, [], ctx.title_exclude) or not is_relevant(j):
                        continue
                    res.jobs.append(j)
                    got += 1
                if not token:
                    break
            if res.calls >= budget:
                break
    except Exception as e:  # noqa: BLE001 — never raise
        res.errors.append(_err(e, key))
    return res
