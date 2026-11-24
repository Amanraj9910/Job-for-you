"""BambooHR careers (Tier A-minus, public careers-site JSON).

List:   GET https://{slug}.bamboohr.com/careers/list         (header Accept: application/json)
        -> {"meta": {"totalCount": n}, "result": [{id, jobOpeningName, departmentLabel, employmentStatusLabel,
           location {city, state}, atsLocation {country, state, province, city}, isRemote, locationType}]}
Detail: GET https://{slug}.bamboohr.com/careers/{id}/detail  -> {"result": {"jobOpening": {description (HTML),
           compensation, datePosted, location {..., addressCountry}, jobOpeningShareUrl, ...}, "formFields"}}
Apply:  https://{slug}.bamboohr.com/careers/{id}
Bad slug: **302 -> https://www.bamboohr.com** (redirects are NOT followed; any 3xx = not found / no careers
site, e.g. 302 -> /login.php when the tenant exists but has no public careers page). Unknown job id -> 404.
locationType: "0" on-site, "1" remote, "2" hybrid.
"""
from __future__ import annotations

from ..models import Job, RemoteType
from .base import SearchContext, SourceError, SourceResult, finalize, register, title_matches
from .workable import BudgetExhausted, Calls, keep_location, note_error, pretty_company, run_boards

NAME = "bamboohr"
_JSON = {"Accept": "application/json"}
_LOCTYPE = {"0": RemoteType.ONSITE, "1": RemoteType.REMOTE, "2": RemoteType.HYBRID}


def base_url(slug: str) -> str:
    return f"https://{slug}.bamboohr.com/careers"


def list_jobs(slug: str, ctx: SearchContext, calls: Calls | None = None) -> list[dict]:
    """Raw list rows. Raises SourceError('not_found') on a redirect (bad slug) or 404."""
    url = f"{base_url(slug)}/list"
    kw = {"headers": _JSON, "follow_redirects": False}
    r = calls.get(url, **kw) if calls else ctx.http.get(url, **kw)
    if 300 <= r.status_code < 400:
        raise SourceError(f"{NAME}:{slug}: not_found (redirect {r.status_code} -> {r.headers.get('location')})")
    if r.status_code == 404:
        raise SourceError(f"{NAME}:{slug}: not_found (404)")
    try:
        data = r.json()
    except ValueError as e:
        raise SourceError(f"{NAME}:{slug}: non-JSON response") from e
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise SourceError(f"{NAME}:{slug}: unexpected payload")
    return data["result"]


def fetch_detail(slug: str, job_id, ctx: SearchContext, calls: Calls | None = None) -> dict | None:
    """jobOpening dict, or None when the job is gone (404 / redirect)."""
    url = f"{base_url(slug)}/{job_id}/detail"
    kw = {"headers": _JSON, "follow_redirects": False}
    r = calls.get(url, **kw) if calls else ctx.http.get(url, **kw)
    if r.status_code == 404 or 300 <= r.status_code < 400:
        return None
    data = r.json()
    return ((data or {}).get("result") or {}).get("jobOpening")


def _loc(row: dict, d: dict | None = None) -> tuple[str | None, str | None]:
    """(location string, country) from list row / detail."""
    d = d or {}
    ats = row.get("atsLocation") or d.get("atsLocation") or {}
    loc = d.get("location") or row.get("location") or {}
    city = loc.get("city") or ats.get("city")
    state = loc.get("state") or ats.get("state") or ats.get("province")
    country = loc.get("addressCountry") or ats.get("country")
    parts = [p.strip() for p in (city, state, country) if isinstance(p, str) and p.strip()]
    return (", ".join(dict.fromkeys(parts)) or None), (country or None)


def _remote(row: dict, d: dict | None = None) -> tuple[RemoteType, bool | None]:
    d = d or {}
    lt = str(d.get("locationType") or row.get("locationType") or "")
    rtype = _LOCTYPE.get(lt, RemoteType.UNKNOWN)
    flag = row.get("isRemote")
    if flag is True:
        rtype = RemoteType.REMOTE
    return rtype, (True if rtype == RemoteType.REMOTE else None)


def parse_job(slug: str, row: dict, detail: dict | None = None, company: str | None = None) -> Job:
    d = detail or {}
    jid = row.get("id") or d.get("id")
    location, country = _loc(row, d)
    rtype, is_remote = _remote(row, d)
    url = f"{base_url(slug)}/{jid}"
    job = Job(
        source=NAME,
        source_job_id=f"{slug}:{jid}",
        title=(row.get("jobOpeningName") or d.get("jobOpeningName") or "").strip(),
        company=company or pretty_company(slug),
        source_url=d.get("jobOpeningShareUrl") or url,
        apply_url=url,
        ats_type="bamboohr",
        ats_slug=slug,
        location=location,
        country=country,
        remote_type=rtype,
        posted_at=d.get("datePosted"),
        jd_text=d.get("description") or "",
        department=row.get("departmentLabel") or d.get("departmentLabel") or None,
        employment_type=row.get("employmentStatusLabel") or d.get("employmentStatusLabel") or None,
        raw={"id": jid},
    )
    return finalize(job, jd_is_html=True, salary_text=d.get("compensation") or None, is_remote=is_remote)


def fetch_board(slug: str, ctx: SearchContext, *, result: SourceResult | None = None,
                limit: int | None = None, company: str | None = None) -> list[Job]:
    """List a BambooHR board, filter title + home-country/remote, then fetch details for kept jobs only."""
    result = result if result is not None else SourceResult(source=NAME, jobs=[])
    calls = Calls(ctx, result, NAME)
    limit = ctx.limit if limit is None else limit
    out: list[Job] = []
    for row in list_jobs(slug, ctx, calls):
        if limit and len(out) >= limit:
            break
        if not title_matches(row.get("jobOpeningName") or "", ctx.title_include, ctx.title_exclude):
            continue
        location, country = _loc(row)
        _, is_remote = _remote(row)
        if not keep_location(location, country, is_remote=is_remote):
            continue
        try:
            detail = fetch_detail(slug, row.get("id"), ctx, calls)
        except BudgetExhausted as e:
            note_error(result, e)
            break
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{NAME}:{slug}:{row.get('id')}: detail failed: {type(e).__name__}: {e}")
            detail = {}
        if detail is None:
            continue
        out.append(parse_job(slug, row, detail, company))
    return out


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return run_boards(NAME, "bamboohr", ctx, fetch_board)
