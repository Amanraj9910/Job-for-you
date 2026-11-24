"""Greenhouse Job Board API (Tier A1).

List:   GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs            (no content, cheap)
Detail: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}?pay_transparency=true
Form:   GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}?questions=true
A bad board token returns 404. Details are fetched only for jobs that pass the title + location filters.

This module also hosts the small helpers shared by the ATS-core sources (lever, ashby, smartrecruiters):
``CallCounter`` (call counting + per-run budget) and ``location_ok`` (home-country / remote-OK filter).
"""
from __future__ import annotations

import re

from ..models import Job, RemoteType
from ..normalize import foreign_hint, is_home, normalize_location, ok_scopes, remote_scope
from ..sanitize import html_to_text
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches

NAME = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards"


# ------------------------------------------------------------------ shared ATS helpers
class BudgetExhausted(SourceError):
    """The per-run call budget for this source is used up."""


def note_error(result: SourceResult, err) -> None:
    """Append an error message once (budget exhaustion can surface in several places)."""
    msg = str(err)
    if msg not in result.errors:
        result.errors.append(msg)


class CallCounter:
    """Counts HTTP calls into ``result.calls`` and enforces ``ctx.budgets[source]`` (max calls per run)."""

    def __init__(self, ctx: SearchContext, result: SourceResult, source: str):
        self.ctx, self.result, self.source = ctx, result, source
        b = ctx.budgets.get(source) if isinstance(ctx.budgets, dict) else None
        self.budget = int(b) if isinstance(b, (int, float)) or (isinstance(b, str) and b.isdigit()) else None

    def get_json(self, url: str, **kw):
        if self.budget is not None and self.result.calls >= self.budget:
            raise BudgetExhausted(f"{self.source}: budget_exhausted ({self.budget} calls)")
        self.result.calls += 1
        return self.ctx.http.get_json(url, **kw)


_AMBIGUOUS = re.compile(r"\b(multiple|various|several|flexible|tbd|any|offices?|locations?|hybrid)\b", re.IGNORECASE)


def _indian(text: str | None, country: str | None = None) -> bool:
    """Located in the user's home country (name kept for older imports; see geo.is_home)."""
    return is_home(text, country)


def location_ok(location: str | None, country: str | None = None, *, is_remote: bool | None = None,
                extra: str | None = None) -> bool:
    """Home-country / remote-OK filter shared by the ATS-core sources (home country = your profile).

    Keeps: jobs located in your home country (the location or extra text names it or one of its cities),
    remote jobs whose scope is in your ``remote_ok_scopes``, bare "Remote" with no foreign place, unknown or
    ambiguous locations ("Multiple locations"). Drops everything clearly abroad, including remote jobs
    restricted to other regions (for an India-based profile: "Remote, EMEA", "Remote - US", "Remote, SF, CA").
    """
    blob = " ".join(x for x in (location, extra) if x)
    if _indian(blob, country):
        return True
    loc = normalize_location(location, None, is_remote=is_remote, extra_text=extra)
    scope = set(loc["remote_scope"]) | set(remote_scope(blob))
    if scope & ok_scopes():          # "Remote - US or APAC" is fine
        return True
    if scope:                      # restricted to other regions (US, EU, UK, EMEA, ...)
        return False
    if foreign_hint(blob):
        return False
    if country:                    # known foreign country
        return False
    if not blob.strip():
        return True                # unknown location: let scoring decide
    if loc["remote_type"] == RemoteType.REMOTE:
        return True                # bare "Remote"
    return bool(_AMBIGUOUS.search(blob))


def pretty_company(slug: str) -> str:
    """Best-effort display name for boards whose API does not return one (lever, ashby)."""
    s = re.sub(r"[-_]+", " ", slug or "").strip()
    return s.title() if s.islower() else s


BOARD_TOTAL_CAP = 3000   # max jobs one company-board source keeps per run


def remaining(ctx: SearchContext, result: SourceResult) -> int:
    return max(0, (ctx.limit or 0) - len(result.jobs)) if ctx.limit else 10**9


def run_boards(name: str, ats: str, ctx: SearchContext, fetch) -> SourceResult:
    """Iterate ctx.boards[ats], calling fetch(slug, ctx, result=, limit=) per board; never raises."""
    result = SourceResult(source=name, jobs=[])
    try:
        boards = list(ctx.boards.get(ats, []) or [])
    except Exception as e:  # noqa: BLE001
        result.errors.append(f"{name}: bad boards config: {e!r}")
        return result
    if not boards:  # nothing to poll is not a failure
        return result
    seen: set[str] = set()
    for slug in boards:
        # ctx.limit is per board here (a busy board must not starve the others); BOARD_TOTAL_CAP bounds the run
        if len(result.jobs) >= BOARD_TOTAL_CAP:
            break
        left = min(ctx.limit or BOARD_TOTAL_CAP, BOARD_TOTAL_CAP - len(result.jobs))
        try:
            for j in fetch(slug, ctx, result=result, limit=left):
                if j.source_job_id not in seen:
                    seen.add(j.source_job_id)
                    result.jobs.append(j)
        except BudgetExhausted as e:
            note_error(result, e)
            break
        except SourceError as e:
            result.errors.append(str(e))
        except Exception as e:  # noqa: BLE001 - a source must never raise
            result.errors.append(f"{name}:{slug}: {type(e).__name__}: {e}")
    return result


# ------------------------------------------------------------------ greenhouse
def _salary(job: Job, ranges: list | None) -> None:
    for r in ranges or []:
        lo, hi = r.get("min_cents"), r.get("max_cents")
        if lo is None and hi is None:
            continue
        job.salary_min = lo / 100 if lo is not None else None
        job.salary_max = hi / 100 if hi is not None else None
        job.salary_currency = (r.get("currency_type") or None)
        blob = f"{r.get('title') or ''} {r.get('blurb') or ''}".lower()
        job.salary_period = "hour" if "hour" in blob else "month" if "month" in blob else "year"
        return


def parse_job(slug: str, item: dict, detail: dict | None = None) -> Job:
    """Build a finalized Job from a list item and (optionally) its detail payload."""
    d = detail or {}
    src = {**item, **d}
    loc = (src.get("location") or {}).get("name") or None
    offices = [o.get("location") or o.get("name") for o in d.get("offices") or [] if o]
    url = src.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{src.get('id')}"
    depts = [x.get("name") for x in d.get("departments") or [] if x.get("name")]
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{src.get('id')}",
        title=(src.get("title") or "").strip(),
        company=(src.get("company_name") or pretty_company(slug)).strip(),
        source_url=url,
        apply_url=url,
        ats_type="greenhouse",
        ats_slug=slug,
        location=loc,
        posted_at=src.get("first_published") or src.get("updated_at"),
        jd_text=d.get("content") or "",
        department=depts[0] if depts else None,
        raw={"id": src.get("id"), "internal_job_id": src.get("internal_job_id"),
             "requisition_id": src.get("requisition_id")},
    )
    _salary(job, d.get("pay_input_ranges"))
    extra = "; ".join(o for o in offices if o and o != loc) or None
    return finalize(job, jd_is_html=True, location_extra=extra)


def list_jobs(slug: str, ctx: SearchContext, counter: CallCounter | None = None) -> list[dict]:
    """Raw list items for a board. Raises SourceError('not_found') on a bad token."""
    url = f"{API}/{slug}/jobs"
    data = counter.get_json(url) if counter else ctx.http.get_json(url)
    if data is None:
        raise SourceError(f"{NAME}:{slug}: not_found (404)")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError(f"{NAME}:{slug}: unexpected payload")
    return data["jobs"]


def fetch_job(slug: str, job_id, ctx: SearchContext, counter: CallCounter | None = None) -> dict | None:
    url = f"{API}/{slug}/jobs/{job_id}"
    params = {"pay_transparency": "true"}
    return counter.get_json(url, params=params) if counter else ctx.http.get_json(url, params=params)


def fetch_board(slug: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """List a board, filter titles + locations, then fetch details only for kept jobs.

    Raises SourceError for a bad token (404) / bad payload; per-job errors go to ``result.errors``.
    """
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    counter = CallCounter(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    out: list[Job] = []
    for item in list_jobs(slug, ctx, counter):
        if limit and len(out) >= limit:
            break
        title = item.get("title") or ""
        if not title_matches(title, ctx.title_include, ctx.title_exclude):
            continue
        if not location_ok((item.get("location") or {}).get("name")):
            continue
        try:
            detail = fetch_job(slug, item.get("id"), ctx, counter)
        except BudgetExhausted as e:  # keep what we already have
            note_error(result, e)
            break
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{slug}:{item.get('id')}: detail failed: {type(e).__name__}: {e}")
            continue
        if detail is None:  # closed between list and detail
            continue
        out.append(parse_job(slug, item, detail))
    return out


def fetch_questions(slug: str, job_id, ctx: SearchContext | None = None) -> list[dict]:
    """Application form for Stage 7: [{label, name, type, required, options, description, section}].

    One entry per form *field* (a question like Resume/CV has a file field and a textarea field).
    options = [{label, value}] for selects/checkboxes. Raises SourceError when the job is gone.
    """
    ctx = ctx or SearchContext()
    d = ctx.http.get_json(f"{API}/{slug}/jobs/{job_id}", params={"questions": "true"})
    if d is None:
        raise SourceError(f"{NAME}:{slug}:{job_id}: not_found (404)")
    out: list[dict] = []

    def add_std(questions, section: str) -> None:
        for q in questions or []:
            desc = html_to_text(q.get("description")) if q.get("description") else None
            for f in q.get("fields") or [{}]:
                out.append({
                    "label": " ".join((q.get("label") or "").split()),
                    "name": f.get("name"),
                    "type": f.get("type"),
                    "required": bool(q.get("required")),
                    "options": [{"label": v.get("label"), "value": v.get("value")}
                                for v in f.get("values") or []],
                    "description": desc,
                    "section": section,
                })

    add_std(d.get("questions"), "questions")
    add_std(d.get("location_questions"), "location")
    for block in d.get("compliance") or []:
        add_std(block.get("questions"), f"compliance:{block.get('type') or ''}".rstrip(":"))
    demo = d.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        out.append({
            "label": " ".join((q.get("label") or "").split()),
            "name": f"demographic_{q.get('id')}",
            "type": q.get("type"),
            "required": bool(q.get("required")),
            "options": [{"label": o.get("label"), "value": o.get("id")} for o in q.get("answer_options") or []],
            "description": None,
            "section": "demographic",
        })
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "greenhouse", ctx, fetch_board)
