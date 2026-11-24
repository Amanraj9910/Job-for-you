"""Workable (Tier A-minus, public undocumented endpoints).

List:   GET  https://apply.workable.com/api/v1/widget/accounts/{slug}
        (all published jobs incl. locations + ``telecommuting``; one row per job *location*, so rows are
        merged by shortcode; no description)
Detail: GET  https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}
        (description / requirements / benefits HTML, ``workplace`` remote|hybrid|on_site)
Alt:    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs  {"query":"", ...}  (paginated, 10/page;
        not used: the widget returns the whole board in one call)
Apply:  https://apply.workable.com/{slug}/j/{shortcode}/
A bad slug returns 404 on all three. A valid board with no openings returns ``{"name":..., "jobs": []}``.

This module also hosts small helpers shared by the "ats-more" group (workable, recruitee, bamboohr, breezy,
keka, zohorecruit, workday): ``Calls`` (call counting + per-run budget), ``keep_location`` (home-country / remote
filter), ``run_boards`` (iterate a watchlist; never raises).
"""
from __future__ import annotations

import re

from ..geo import is_home_country_code
from ..models import Job, RemoteType
from ..normalize import normalize_location, ok_scopes
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches

NAME = "workable"
BASE = "https://apply.workable.com"


# ====================================================================== shared helpers (ats-more group)
class BudgetExhausted(SourceError):
    """The per-run HTTP call budget for this source (ctx.budgets[name]) is used up."""


def note_error(result: SourceResult, err) -> None:
    msg = str(err)
    if msg not in result.errors:
        result.errors.append(msg)


class Calls:
    """Counts HTTP calls into ``result.calls`` and enforces ``ctx.budgets[source]`` (max calls per run)."""

    def __init__(self, ctx: SearchContext, result: SourceResult, source: str):
        self.ctx, self.result, self.source = ctx, result, source
        b = ctx.budgets.get(source) if isinstance(ctx.budgets, dict) else None
        try:
            self.budget = int(b) if b is not None else None
        except (TypeError, ValueError):
            self.budget = None

    def _tick(self) -> None:
        if self.budget is not None and self.result.calls >= self.budget:
            raise BudgetExhausted(f"{self.source}: budget_exhausted ({self.budget} calls)")
        self.result.calls += 1

    @property
    def left(self) -> int:
        return 10**9 if self.budget is None else max(0, self.budget - self.result.calls)

    def get(self, url: str, **kw):
        self._tick()
        return self.ctx.http.get(url, **kw)

    def post(self, url: str, **kw):
        self._tick()
        return self.ctx.http.post(url, **kw)

    def get_json(self, url: str, **kw):
        self._tick()
        return self.ctx.http.get_json(url, **kw)




def keep_location(location: str | None, country: str | None = None, *, is_remote: bool | None = None,
                  extra: str | None = None) -> bool:
    """Home-country or acceptable-remote filter used before any detail fetch.

    Keep: location tier 1-3 (your home country), remote with no scope or a scope in your remote_ok_scopes
    (unless an explicit foreign country pins it), unknown location. Drop: onsite/hybrid abroad, remote scoped
    to other regions, remote pinned to a foreign country.
    """
    loc = normalize_location(location, country, is_remote=is_remote, extra_text=extra)
    if loc["location_tier"] in (1, 2, 3):
        return True
    scope = set(loc["remote_scope"])
    if loc["remote_type"] == RemoteType.REMOTE:
        if scope & ok_scopes():
            return True
        if scope:
            return False
        return not (country and not is_home_country_code(country))
    return loc["location_tier"] is None


def pretty_company(slug: str) -> str:
    s = re.sub(r"[-_]+", " ", slug or "").strip()
    return s.title() if s.islower() else s


BOARD_TOTAL_CAP = 3000   # max jobs one company-board source keeps per run


def remaining(ctx: SearchContext, result: SourceResult) -> int:
    return max(0, (ctx.limit or 0) - len(result.jobs)) if ctx.limit else 10**9


def run_boards(name: str, ats: str, ctx: SearchContext, fetch) -> SourceResult:
    """Iterate ctx.boards[ats], calling fetch(board, ctx, result=, limit=) per board. Never raises."""
    result = SourceResult(source=name, jobs=[])
    try:
        boards = list((ctx.boards or {}).get(ats, []) or [])
    except Exception as e:  # noqa: BLE001
        result.errors.append(f"{name}: bad boards config: {e!r}")
        return result
    seen: set[str] = set()
    for board in boards:
        # ctx.limit is per board here (a busy board must not starve the others); BOARD_TOTAL_CAP bounds the run
        if len(result.jobs) >= BOARD_TOTAL_CAP:
            break
        left = min(ctx.limit or BOARD_TOTAL_CAP, BOARD_TOTAL_CAP - len(result.jobs))
        try:
            for j in fetch(board, ctx, result=result, limit=left):
                if j.source_job_id not in seen:
                    seen.add(j.source_job_id)
                    result.jobs.append(j)
        except BudgetExhausted as e:
            note_error(result, e)
            break
        except SourceError as e:
            result.errors.append(str(e))
        except Exception as e:  # noqa: BLE001 - a source must never raise
            result.errors.append(f"{name}:{board}: {type(e).__name__}: {e}")
    return result


_WORKPLACE = {"remote": RemoteType.REMOTE, "hybrid": RemoteType.HYBRID, "on_site": RemoteType.ONSITE,
              "onsite": RemoteType.ONSITE}


# ====================================================================== workable
def _loc_str(loc: dict) -> str:
    parts = [loc.get("city"), loc.get("region") or loc.get("state"), loc.get("country")]
    out: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if p and p not in out:
            out.append(p)
    return ", ".join(out)


def list_jobs(slug: str, ctx: SearchContext, calls: Calls | None = None) -> dict:
    """Widget payload with rows merged by shortcode -> {"name": str, "jobs": [row + _locs]}.

    Raises SourceError('not_found') on a bad slug.
    """
    url = f"{BASE}/api/v1/widget/accounts/{slug}"
    data = calls.get_json(url) if calls else ctx.http.get_json(url)
    if data is None:
        raise SourceError(f"{NAME}:{slug}: not_found (404)")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError(f"{NAME}:{slug}: unexpected payload")
    merged: dict[str, dict] = {}
    for row in data["jobs"]:
        sc = row.get("shortcode")
        if not sc:
            continue
        locs = [x for x in (row.get("locations") or []) if not x.get("hidden")] or \
            [{"city": row.get("city"), "region": row.get("state"), "country": row.get("country")}]
        if sc in merged:
            merged[sc]["_locs"].extend(locs)
        else:
            merged[sc] = {**row, "_locs": list(locs)}
    return {"name": data.get("name") or pretty_company(slug), "jobs": list(merged.values())}


def fetch_detail(slug: str, shortcode: str, ctx: SearchContext, calls: Calls | None = None) -> dict | None:
    url = f"{BASE}/api/v2/accounts/{slug}/jobs/{shortcode}"
    return calls.get_json(url) if calls else ctx.http.get_json(url)


def _pick_location(locs: list[str]) -> tuple[str | None, str | None]:
    """(primary, extra): prefer a home-country location as primary."""
    locs = [x for x in dict.fromkeys(locs) if x]
    if not locs:
        return None, None
    primary = next((x for x in locs if normalize_location(x)["location_tier"] in (1, 2, 3)), locs[0])
    rest = [x for x in locs if x != primary]
    return primary, ("; ".join(rest) or None)


def parse_job(slug: str, company: str, row: dict, detail: dict | None = None) -> Job:
    d = detail or {}
    sc = row.get("shortcode") or d.get("shortcode")
    locs = [_loc_str(x) for x in row.get("_locs") or d.get("locations") or []]
    location, extra = _pick_location(locs)
    is_remote = bool(row.get("telecommuting") or d.get("remote"))
    url = f"{BASE}/{slug}/j/{sc}/"
    jd = "\n".join(x for x in (d.get("description"), d.get("requirements"), d.get("benefits")) if x)
    dept = row.get("department")
    if not dept:
        dd = d.get("department")
        dept = dd[0] if isinstance(dd, list) and dd else dd if isinstance(dd, str) else None
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{sc}",
        title=(row.get("title") or d.get("title") or "").strip(),
        company=company,
        source_url=url,
        apply_url=url,
        ats_type="workable",
        ats_slug=slug,
        location=location,
        posted_at=d.get("published") or row.get("published_on") or row.get("created_at"),
        jd_text=jd,
        department=dept or None,
        employment_type=row.get("employment_type") or d.get("type") or None,
        remote_type=_WORKPLACE.get((d.get("workplace") or "").lower(), RemoteType.UNKNOWN),
        raw={"shortcode": sc, "id": d.get("id")},
    )
    if job.remote_type == RemoteType.UNKNOWN and is_remote:
        job.remote_type = RemoteType.REMOTE
    return finalize(job, jd_is_html=True, is_remote=is_remote or None, location_extra=extra)


def fetch_board(slug: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None) -> list[Job]:
    """List a Workable board, filter title + home-country/remote, then fetch details for kept jobs only."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    board = list_jobs(slug, ctx, calls)
    out: list[Job] = []
    for row in board["jobs"]:
        if limit and len(out) >= limit:
            break
        if not title_matches(row.get("title") or "", ctx.title_include, ctx.title_exclude):
            continue
        locs = [_loc_str(x) for x in row["_locs"]]
        remote = bool(row.get("telecommuting"))
        if not any(keep_location(x or None, is_remote=remote) for x in (locs or [""])):
            continue
        try:
            detail = fetch_detail(slug, row["shortcode"], ctx, calls)
        except BudgetExhausted as e:
            note_error(result, e)
            break
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{slug}:{row.get('shortcode')}: detail failed: {type(e).__name__}: {e}")
            detail = {}
        if detail is None:  # closed between list and detail
            continue
        out.append(parse_job(slug, board["name"], row, detail))
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "workable", ctx, fetch_board)
