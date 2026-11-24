"""Common contract for job sources."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..classify import classify_channel
from ..models import Job
from ..normalize import normalize_location, parse_experience, parse_posted_at, parse_salary
from ..sanitize import sanitize_jd
from ..util.http import Http, http


class SourceError(RuntimeError):
    """Raised for a source-level failure (blocked, bad response). Recorded in source_health."""


@dataclass
class SearchContext:
    queries: list[str] = field(default_factory=list)          # free-text queries for search APIs
    locations: list[str] = field(default_factory=list)        # e.g. Bengaluru, Indore, Remote India
    title_include: list[str] = field(default_factory=list)    # keyword filter for ATS boards
    title_exclude: list[str] = field(default_factory=list)
    budgets: dict = field(default_factory=dict)
    boards: dict[str, list[str]] = field(default_factory=dict)  # ats -> watchlist slugs to poll
    hours_old: int = 168
    limit: int = 50                                            # max jobs per source call
    client: Http | None = None

    @property
    def http(self) -> Http:
        return self.client or http()


@dataclass
class SourceResult:
    source: str
    jobs: list[Job]
    errors: list[str] = field(default_factory=list)
    calls: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors or bool(self.jobs)


def _kw_rx(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words if w and w.strip()]
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


def title_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    inc, exc = _kw_rx(include), _kw_rx(exclude)
    if exc and exc.search(title or ""):
        return False
    return bool(inc.search(title or "")) if inc else True


def finalize(job: Job, *, jd_is_html: bool = True, salary_text: str | None = None,
             is_remote: bool | None = None, location_extra: str | None = None) -> Job:
    """Fill derived fields every source should have: sanitized JD, location tier/scope, exp, salary, channel."""
    job.jd_text = sanitize_jd(job.jd_text, is_html=jd_is_html)
    loc = normalize_location(job.location, job.country, is_remote=is_remote, extra_text=location_extra)
    job.location = loc["location"] or job.location
    job.country = loc["country"] or job.country
    if job.remote_type.value == "unknown":
        job.remote_type = loc["remote_type"]
    job.remote_scope = job.remote_scope or loc["remote_scope"]
    job.location_tier = job.location_tier or loc["location_tier"]
    if job.exp_min is None and job.exp_max is None:
        job.exp_min, job.exp_max = parse_experience(job.jd_text[:6000])
    if job.salary_min is None and job.salary_max is None:
        lo, hi, cur, per = parse_salary(salary_text) if salary_text else (None, None, None, None)
        job.salary_min, job.salary_max = lo, hi
        job.salary_currency = job.salary_currency or cur
        job.salary_period = job.salary_period or per
    if isinstance(job.posted_at, (str, int, float)):
        job.posted_at = parse_posted_at(job.posted_at)
    channel, ats, slug = classify_channel(job.apply_url, source=job.source)
    job.channel = channel
    job.ats_type = job.ats_type or ats
    job.ats_slug = job.ats_slug or slug
    return job


# ---------------- registry ----------------
SourceFn = Callable[[SearchContext], SourceResult]
REGISTRY: dict[str, SourceFn] = {}


def register(name: str):
    def deco(fn: SourceFn) -> SourceFn:
        REGISTRY[name] = fn
        return fn
    return deco
