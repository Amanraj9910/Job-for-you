"""We Work Remotely category RSS feeds (A10), parsed with feedparser.

Feeds: https://weworkremotely.com/categories/<category>.rss . Item title is "Company: Job title"; the custom
``<region>`` element (e.g. "Anywhere in the World", "Remote - Americas", "Helsinki") drives remote_scope.
WWR is a board, so apply_url is the WWR job page (channel manual_other).
"""
from __future__ import annotations

import re

import feedparser

from ..models import Job, RemoteType
from ..normalize import parse_posted_at
from .base import SearchContext, SourceResult, finalize, register
from .himalayas import geo_ok, safe_location
from .remoteok import wanted

NAME = "wwr"
BASE = "https://weworkremotely.com/categories/{}.rss"
DEFAULT_FEEDS = [
    "remote-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-back-end-programming-jobs",
    "remote-devops-sysadmin-jobs",
]

_REGION_EXTRA = [(re.compile(r"\bamericas?\b", re.I), "AMERICAS")]


def feed_url(category: str) -> str:
    return category if category.startswith("http") else BASE.format(category)


def fetch_feed(category: str, ctx: SearchContext) -> list[dict]:
    """One raw call; returns feedparser entries ([] on 404). Raises on network/HTTP errors."""
    r = ctx.http.get(feed_url(category))
    if r.status_code == 404:
        return []
    parsed = feedparser.parse(r.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.get('bozo_exception')}")
    return list(parsed.entries)


def split_title(raw: str) -> tuple[str, str]:
    """'Company: Title' -> (company, title)."""
    company, sep, title = (raw or "").partition(": ")
    return (company.strip(), title.strip()) if sep else ("", (raw or "").strip())


def parse_entry(e: dict) -> Job | None:
    company, title = split_title(e.get("title") or "")
    link = e.get("link") or e.get("id")
    if not title or not company or not link:
        return None
    region = safe_location(e.get("region"))
    job = Job(
        source=NAME,
        source_job_id=link.rstrip("/").rsplit("/", 1)[-1],
        title=title,
        company=company,
        source_url=link,
        apply_url=link,
        location=f"Remote ({region})" if region else "Remote",
        remote_type=RemoteType.REMOTE,
        posted_at=parse_posted_at(e.get("published")),
        jd_text=e.get("summary") or "",
        department=next((t.get("term") for t in e.get("tags") or [] if t.get("term")), None),
        raw={"guid": e.get("id")},
    )
    job = finalize(job, is_remote=True)
    if not job.remote_scope:
        job.remote_scope = [tok for rx, tok in _REGION_EXTRA if rx.search(region)]
    return job


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(NAME, [])
    feeds = list(ctx.budgets.get("wwr_feeds") or DEFAULT_FEEDS)
    budget = int(ctx.budgets.get(NAME, len(feeds)))
    seen: set[str] = set()
    for cat in feeds:
        if len(res.jobs) >= ctx.limit or res.calls >= budget:
            break
        try:
            res.calls += 1
            entries = fetch_feed(cat, ctx)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{NAME} {cat}: {type(e).__name__}: {e}")
            continue
        for e in entries:
            if len(res.jobs) >= ctx.limit:
                break
            try:
                company, title = split_title(e.get("title") or "")
                tags = [t.get("term") or "" for t in e.get("tags") or []]
                if not wanted(title, tags, ctx):
                    continue
                job = parse_entry(e)
            except Exception as ex:  # noqa: BLE001
                res.errors.append(f"{NAME} parse {e.get('link')}: {type(ex).__name__}: {ex}")
                continue
            if job and job.source_job_id not in seen and geo_ok(job):
                seen.add(job.source_job_id)
                res.jobs.append(job)
    return res
