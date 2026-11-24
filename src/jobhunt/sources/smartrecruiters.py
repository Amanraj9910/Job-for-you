"""SmartRecruiters Posting API (Tier A4).

List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=N&country=<home cc>
        plus one remote pass: ...?limit=100&q=remote (kept only if the posting is remote and open to you)
Detail: GET https://api.smartrecruiters.com/v1/companies/{id}/postings/{postingId}   (jobAd.sections.* HTML)
A wrong company id returns 200 with totalFound 0, indistinguishable from an empty board: reported as
``empty_or_unknown``. Ids are case-insensitive. Details are fetched only for jobs that pass the filters.
"""
from __future__ import annotations

import re

from ..geo import current
from ..models import Job, RemoteType
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .greenhouse import BudgetExhausted, CallCounter, location_ok, note_error, run_boards

NAME = "smartrecruiters"
API = "https://api.smartrecruiters.com/v1/companies"
PAGE = 100
MAX_PAGES = 5            # 500 home-country postings per company is plenty
_SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")


def list_postings(company_id: str, ctx: SearchContext, counter: CallCounter | None = None, *,
                  country: str | None = "in", q: str | None = None) -> tuple[list[dict], int]:
    """-> (postings, totalFound) following offset pagination."""
    out: list[dict] = []
    total = 0
    for page in range(MAX_PAGES):
        params: dict = {"limit": PAGE, "offset": page * PAGE}
        if country:
            params["country"] = country
        if q:
            params["q"] = q
        url = f"{API}/{company_id}/postings"
        data = counter.get_json(url, params=params) if counter else ctx.http.get_json(url, params=params)
        if data is None:
            raise SourceError(f"{NAME}:{company_id}: not_found (404)")
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise SourceError(f"{NAME}:{company_id}: unexpected payload")
        content = data["content"]
        total = int(data.get("totalFound") or 0)
        out.extend(content)
        if len(content) < PAGE or len(out) >= total:
            break
    return out, total


def fetch_posting(company_id: str, posting_id: str, ctx: SearchContext,
                  counter: CallCounter | None = None) -> dict | None:
    url = f"{API}/{company_id}/postings/{posting_id}"
    return counter.get_json(url) if counter else ctx.http.get_json(url)


def _full_location(loc: dict) -> str | None:
    full = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                                             loc.get("country")) if x)
    full = re.sub(r"(,\s*)+", ", ", full or "").strip(" ,")
    return full or None


def _remote_flag(loc: dict) -> bool | None:
    return True if loc.get("remote") else None


def _jd_html(detail: dict) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    parts = []
    for key in _SECTIONS:
        s = sections.get(key) or {}
        if s.get("text"):
            if s.get("title"):
                parts.append(f"<h3>{s['title']}</h3>")
            parts.append(s["text"])
    return "\n".join(parts)


def parse_posting(company_id: str, item: dict, detail: dict | None = None) -> Job:
    d = detail or {}
    src = {**item, **d}
    loc = src.get("location") or {}
    pid = str(src.get("id"))
    rtype = RemoteType.REMOTE if loc.get("remote") else RemoteType.HYBRID if loc.get("hybrid") \
        else RemoteType.UNKNOWN
    fallback = f"https://jobs.smartrecruiters.com/{company_id}/{pid}"
    job = Job(
        source=NAME,
        source_job_id=f"{company_id.lower()}:{pid}",
        title=(src.get("name") or "").strip(),
        company=((src.get("company") or {}).get("name") or company_id).strip(),
        source_url=src.get("postingUrl") or fallback,
        apply_url=src.get("applyUrl") or src.get("postingUrl") or fallback,
        ats_type="smartrecruiters",
        ats_slug=company_id,
        location=_full_location(loc),
        country=(loc.get("country") or "").upper() or None,
        remote_type=rtype,
        posted_at=src.get("releasedDate"),
        jd_text=_jd_html(d),
        department=(src.get("department") or {}).get("label") or (src.get("function") or {}).get("label"),
        employment_type=(src.get("typeOfEmployment") or {}).get("label"),
        raw={"id": pid, "uuid": src.get("uuid"), "refNumber": src.get("refNumber")},
    )
    return finalize(job, jd_is_html=True, is_remote=_remote_flag(loc))


def fetch_board(company_id: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None, remote_pass: bool = True) -> list[Job]:
    """Home-country postings (country=<cc>) plus remote postings open to you, title-filtered, with details.

    Raises SourceError('empty_or_unknown') when the company has no postings in either pass.
    """
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    counter = CallCounter(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    items, total = list_postings(company_id, ctx, counter, country=current().home_cc.lower())
    remote_total = 0
    if remote_pass:
        try:
            remote, remote_total = list_postings(company_id, ctx, counter, country=None, q="remote")
            items += [r for r in remote if (r.get("location") or {}).get("remote")]
        except BudgetExhausted as e:
            note_error(result, e)
        except Exception as e:  # noqa: BLE001 - the home-country pass already succeeded
            result.errors.append(f"{NAME}:{company_id}: remote pass failed: {type(e).__name__}: {e}")
    if not items and total == 0 and remote_total == 0:
        raise SourceError(f"{NAME}:{company_id}: empty_or_unknown (totalFound 0)")
    out: list[Job] = []
    seen: set[str] = set()
    for item in items:
        if limit and len(out) >= limit:
            break
        pid = str(item.get("id"))
        if pid in seen:
            continue
        seen.add(pid)
        if not title_matches(item.get("name") or "", ctx.title_include, ctx.title_exclude):
            continue
        loc = item.get("location") or {}
        if not location_ok(_full_location(loc), loc.get("country"), is_remote=_remote_flag(loc)):
            continue
        try:
            detail = fetch_posting(company_id, pid, ctx, counter)
        except BudgetExhausted as e:  # keep what we already have
            note_error(result, e)
            break
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{company_id}:{pid}: detail failed: {type(e).__name__}: {e}")
            continue
        if detail is None:  # closed between list and detail
            continue
        try:
            out.append(parse_posting(company_id, item, detail))
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{company_id}:{pid}: parse failed: {type(e).__name__}: {e}")
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "smartrecruiters", ctx, fetch_board)
