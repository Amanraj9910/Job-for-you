"""Remotive public API (A8).

Endpoint: GET https://remotive.com/api/remote-jobs?search=<q>&limit=<n>  (or ?category=software-development)
ToS: max ~4 fetches/day (more than 2/min is blocked), jobs are 24 h delayed, link back to the Remotive URL and
mention Remotive, never redistribute. The per-day cap is enforced with an on-disk log of call timestamps in
``data/cache/remotive_calls.json`` (``DailyCap``); the Http client additionally spaces remotive.com calls 35 s.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import settings
from ..models import Job, RemoteType
from ..normalize import parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches
from .himalayas import geo_ok, safe_location

NAME = "remotive"
API_URL = "https://remotive.com/api/remote-jobs"
DEFAULT_PER_DAY = 4
DEFAULT_PER_RUN = 2
DEFAULT_CATEGORY = "software-development"
DAY = 24 * 3600


def cache_dir() -> Path:
    return settings().data_dir / "cache"


class DailyCap:
    """Rolling-24h call counter persisted to ``<cache_dir>/<name>_calls.json`` (list of epoch seconds)."""

    def __init__(self, name: str, per_day: int):
        self.name, self.per_day = name, int(per_day)

    @property
    def path(self) -> Path:
        return cache_dir() / f"{self.name}_calls.json"

    def _load(self, now: float) -> list[float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [float(t) for t in data if now - float(t) < DAY]
        except (OSError, ValueError, TypeError):
            return []

    def used(self, now: float | None = None) -> int:
        return len(self._load(now or time.time()))

    def remaining(self, now: float | None = None) -> int:
        return max(0, self.per_day - self.used(now))

    def record(self, now: float | None = None) -> None:
        now = now or time.time()
        calls = self._load(now) + [now]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(calls), encoding="utf-8")


def fetch(ctx: SearchContext, *, query: str | None = None, category: str | None = None,
          limit: int | None = None) -> list[dict]:
    """One raw API call (no cap check). Returns the ``jobs`` list."""
    params: dict = {}
    if query:
        params["search"] = query
    if category:
        params["category"] = category
    if limit:
        params["limit"] = limit
    data = ctx.http.get_json(API_URL, params=params) or {}
    return data.get("jobs") or []


def parse_job(d: dict) -> Job | None:
    title = (d.get("title") or "").strip()
    company = (d.get("company_name") or "").strip()
    if not title or not company or not d.get("id"):
        return None
    crl = safe_location(d.get("candidate_required_location"))
    job = Job(
        source=NAME,
        source_job_id=str(d["id"]),
        title=title,
        company=company,
        source_url=d.get("url"),
        apply_url=d.get("url"),          # ToS: link back to Remotive; it carries the employer's apply link
        location=f"Remote ({crl})" if crl else "Remote",
        remote_type=RemoteType.REMOTE,
        posted_at=parse_posted_at(d.get("publication_date")),
        jd_text=d.get("description") or "",
        department=d.get("category"),
        employment_type=d.get("job_type"),
        raw={"id": d.get("id")},
    )
    return finalize(job, salary_text=d.get("salary") or None, is_remote=True)


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(NAME, [])
    try:
        cap = DailyCap(NAME, ctx.budgets.get("remotive_per_day", DEFAULT_PER_DAY))
        per_run = int(ctx.budgets.get(NAME, DEFAULT_PER_RUN))
        plan: list[tuple[str | None, str | None]] = [(q, None) for q in ctx.queries] or [(None, DEFAULT_CATEGORY)]
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"{NAME}: setup failed: {type(e).__name__}: {e}")
        return res
    seen: set[str] = set()
    for query, category in plan:
        if len(res.jobs) >= ctx.limit or res.calls >= per_run:
            break
        try:
            if cap.remaining() <= 0:
                res.errors.append(f"{NAME}: daily cap reached ({cap.per_day}/24h; see {cap.path.name})")
                break
            cap.record()
            res.calls += 1
            items = fetch(ctx, query=query, category=category, limit=max(ctx.limit, 20))
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{NAME} q={query!r}: {type(e).__name__}: {e}")
            continue
        for d in items:
            if len(res.jobs) >= ctx.limit:
                break
            if not title_matches(d.get("title") or "", ctx.title_include, ctx.title_exclude):
                continue
            try:
                job = parse_job(d)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{NAME} parse {d.get('id')}: {type(e).__name__}: {e}")
                continue
            if job and job.source_job_id not in seen and geo_ok(job):
                seen.add(job.source_job_id)
                res.jobs.append(job)
    return res
