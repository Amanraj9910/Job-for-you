"""Search orchestration: build the context from profile + watchlist, run sources, store, dedupe, health."""
from __future__ import annotations

import importlib
import pkgutil
import sqlite3
import uuid
from datetime import UTC, datetime

from . import db
from .dedupe import assign_canonical
from .profile import search_cfg, title_keywords
from .sources import base as sbase
from .util.logs import get

log = get("search")

# default sources (override with search.sources / search.disabled_sources in preferences.yaml).
# LinkedIn/Naukri are discovery-only: logged-out search, small volume; you apply on the site yourself.
# serpapi runs only when SERPAPI_KEY is set; naukri only for India-based profiles.
DEFAULT_SOURCES = [
    "greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "bamboohr", "breezy", "keka",
    "zohorecruit", "himalayas", "remotive", "remoteok", "wwr", "yc_jobs",
    "jobspy_indeed", "jobspy_glassdoor", "jobspy_naukri", "jobspy_linkedin", "serpapi",
]
WATCHLIST_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "bamboohr",
                 "breezy", "keka", "zohorecruit", "workday"}


def load_sources() -> dict:
    """Import every module in jobhunt.sources so their @register decorators run."""
    import jobhunt.sources as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name in ("base", "discover"):
            continue
        try:
            importlib.import_module(f"jobhunt.sources.{mod.name}")
        except Exception as e:  # noqa: BLE001 — a broken source must not break the run
            log.warning("source module %s failed to import: %s", mod.name, e)
    return sbase.REGISTRY


def watchlist_boards(conn: sqlite3.Connection, max_boards: int = 300) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT ats_type, ats_slug FROM watchlist WHERE state IN ('active','pending') "
        "ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, COALESCE(n_jobs,0) DESC, fail_count ASC "
        "LIMIT ?", (max_boards,)).fetchall()
    boards: dict[str, list[str]] = {}
    for r in rows:
        boards.setdefault(r[0], []).append(r[1])
    return boards


def build_context(conn: sqlite3.Connection, *, tiers: tuple[int, ...] = (1, 2), query: str | None = None,
                  location: str | None = None, hours_old: int | None = None,
                  limit: int | None = None) -> sbase.SearchContext:
    """Search settings from your profile (preferences.yaml → search, title_keywords), overridable per run."""
    s = search_cfg()
    tk = title_keywords()
    qs = [query] if query else [x for t in tiers for x in (s.get("queries") or {}).get(t, [])]
    locs = [location] if location else list(s.get("locations") or []) + list(s.get("remote_locations") or [])
    return sbase.SearchContext(queries=qs, locations=locs, title_include=tk.get("include", []),
                               title_exclude=tk.get("exclude", []), budgets=dict(s.get("budgets") or {}),
                               boards=watchlist_boards(conn, int(s.get("max_boards", 300))),
                               hours_old=hours_old or 24 * int(s.get("posted_within_days", 7)),
                               limit=limit or int(s.get("results_per_call", 30)))


def enabled_sources() -> list[str]:
    s = search_cfg()
    names = s.get("sources") or DEFAULT_SOURCES
    off = set(s.get("disabled_sources") or [])
    return [n for n in names if n not in off]


def run(conn: sqlite3.Connection, sources: list[str] | None = None, ctx: sbase.SearchContext | None = None) -> dict:
    reg = load_sources()
    ctx = ctx or build_context(conn)
    names = sources or [s for s in enabled_sources() if s in reg]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
    summary: dict = {"run_id": run_id, "sources": {}, "new_jobs": 0, "seen_jobs": 0}
    touched: list[str] = []
    for name in names:
        fn = reg.get(name)
        if fn is None:
            summary["sources"][name] = {"ok": False, "error": "not registered"}
            db.record_source_health(conn, run_id, name, False, 0, "not registered")
            continue
        try:
            res = fn(ctx)
        except Exception as e:  # noqa: BLE001 — contract says never raise; be defensive anyway
            res = sbase.SourceResult(source=name, jobs=[], errors=[f"crashed: {e}"])
        new = 0
        for job in res.jobs:
            existed = db.get_job(conn, job.job_id) is not None
            jid = db.upsert_job(conn, job)
            touched.append(jid)
            new += 0 if existed else 1
        _update_watchlist(conn, name, res, ctx)
        err = "; ".join(res.errors)[:1000] or None
        db.record_source_health(conn, run_id, name, res.ok, len(res.jobs), err)
        summary["sources"][name] = {"ok": res.ok, "jobs": len(res.jobs), "new": new, "calls": res.calls,
                                    "errors": res.errors[:5]}
        summary["new_jobs"] += new
        summary["seen_jobs"] += len(res.jobs)
    summary["dedupe"] = assign_canonical(conn, touched) if touched else {"groups": 0, "duplicates": 0}
    db.log_event(conn, "jobhunt", "search_run", None, run_id=run_id, new=summary["new_jobs"],
                 seen=summary["seen_jobs"])
    return summary


def _update_watchlist(conn: sqlite3.Connection, source: str, res: sbase.SourceResult,
                      ctx: sbase.SearchContext) -> None:
    """Mark polled boards; boards producing jobs become active, boards failing 3x in a row go dormant."""
    if source not in WATCHLIST_ATS:
        return
    now = db.now_iso()
    counts: dict[str, int] = {}
    for j in res.jobs:
        if j.ats_slug:
            counts[j.ats_slug.lower()] = counts.get(j.ats_slug.lower(), 0) + 1
    err_text = " ".join(res.errors).lower()
    with db.tx(conn):
        for slug in ctx.boards.get(source, []):
            n = counts.get(slug.lower(), 0)
            if n:
                conn.execute("UPDATE watchlist SET state='active', last_polled=?, last_ok=?, n_jobs=?, "
                             "fail_count=0 WHERE ats_type=? AND ats_slug=?", (now, now, n, source, slug))
            elif slug.lower() in err_text:
                conn.execute("UPDATE watchlist SET last_polled=?, fail_count=fail_count+1, "
                             "state=CASE WHEN fail_count+1 >= 3 THEN 'dormant' ELSE state END "
                             "WHERE ats_type=? AND ats_slug=?", (now, source, slug))
            else:
                conn.execute("UPDATE watchlist SET last_polled=?, last_ok=?, n_jobs=0 WHERE ats_type=? AND "
                             "ats_slug=?", (now, now, source, slug))


def add_to_watchlist(conn: sqlite3.Connection, ats: str, slug: str, name: str | None = None,
                     origin: str = "manual", state: str = "pending") -> bool:
    from .models import company_key
    with db.tx(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO watchlist(ats_type, ats_slug, company_key, name, origin, state, added_at) "
            "VALUES (?,?,?,?,?,?,?)", (ats, slug, company_key(name or slug), name or slug, origin, state, db.now_iso()))
    return cur.rowcount > 0
