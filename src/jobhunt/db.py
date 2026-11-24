"""SQLite store (the source of truth): migrations, upserts, status changes, audit events."""
from __future__ import annotations

import itertools
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings
from .models import Job, company_key

TRACKER_STATUSES = [
    "new", "shortlisted", "review", "applied", "interview", "offer", "rejected", "not_interested", "closed",
    "skipped",
]
# statuses you may set yourself (CLI `db set-status`, or ticks in the jobs report)
USER_EDITABLE = ["applied", "interview", "offer", "rejected", "not_interested", "closed", "shortlisted", "skipped"]
# statuses after which a job no longer belongs in any to-do queue
DONE_STATUSES = {"applied", "interview", "offer", "rejected", "not_interested", "closed", "skipped"}

JSON_COLUMNS = {
    "discovered_via", "remote_scope", "gates_json", "judgment_json", "flags", "aliases", "ratings_json",
    "reasons_json", "sources_json", "detail_json", "value_json",
}

_SCHEMA_V1 = """
CREATE TABLE jobs (
    job_id            TEXT PRIMARY KEY,
    canonical_job_id  TEXT,
    discovered_via    TEXT NOT NULL DEFAULT '[]',
    source            TEXT NOT NULL,
    source_job_id     TEXT NOT NULL,
    source_url        TEXT,
    apply_url         TEXT,
    ats_type          TEXT,
    ats_slug          TEXT,
    company           TEXT NOT NULL,
    company_key       TEXT NOT NULL,
    title             TEXT NOT NULL,
    location          TEXT,
    country           TEXT,
    remote_type       TEXT,
    remote_scope      TEXT NOT NULL DEFAULT '[]',
    location_tier     INTEGER,
    posted_at         TEXT,
    salary_min        REAL,
    salary_max        REAL,
    salary_currency   TEXT,
    salary_period     TEXT,
    exp_min           REAL,
    exp_max           REAL,
    jd_text           TEXT NOT NULL DEFAULT '',
    jd_hash           TEXT,
    department        TEXT,
    employment_type   TEXT,
    channel           TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    is_live           INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'new',
    notes             TEXT,
    status_updated_at TEXT,
    status_updated_by TEXT
);
CREATE INDEX ix_jobs_dedupe ON jobs(company_key, title, location);
CREATE INDEX ix_jobs_canonical ON jobs(canonical_job_id);
CREATE INDEX ix_jobs_status ON jobs(status);
CREATE INDEX ix_jobs_jd_hash ON jobs(jd_hash);

CREATE TABLE scores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    heuristic      REAL,
    gates_json     TEXT,
    judgment_json  TEXT,
    fit_score      REAL,
    confidence     REAL,
    band           TEXT,
    flags          TEXT,
    rubric_version TEXT,
    skill_hash     TEXT,
    packet_hash    TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX ix_scores_job ON scores(job_id, id);

CREATE TABLE companies (
    company_key         TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    domain              TEXT,
    aliases             TEXT NOT NULL DEFAULT '[]',
    verdict             TEXT,
    bond_flag           INTEGER,
    fraud_flag          INTEGER,
    employer_identified INTEGER,
    ratings_json        TEXT,
    reasons_json        TEXT,
    sources_json        TEXT,
    decided_by          TEXT,
    checked_at          TEXT
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    job_id      TEXT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_events_job ON events(job_id);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE source_health (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL,
    source  TEXT NOT NULL,
    ok      INTEGER NOT NULL,
    n_jobs  INTEGER NOT NULL DEFAULT 0,
    error   TEXT,
    ts      TEXT NOT NULL
);
CREATE INDEX ix_source_health_source ON source_health(source, ts);

CREATE TABLE watchlist (
    ats_type    TEXT NOT NULL,
    ats_slug    TEXT NOT NULL,
    company_key TEXT,
    name        TEXT,
    origin      TEXT,
    state       TEXT NOT NULL DEFAULT 'pending',
    added_at    TEXT NOT NULL,
    last_polled TEXT,
    last_ok     TEXT,
    n_jobs      INTEGER,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ats_type, ats_slug)
);
CREATE INDEX ix_watchlist_state ON watchlist(state);

CREATE TABLE probe_state (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""

MIGRATIONS: list[str] = [_SCHEMA_V1]

_sp_counter = itertools.count(1)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and migrate) the DB. Autocommit mode: group writes with `tx(conn)`."""
    p = Path(path) if path is not None else settings().db_path
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA recursive_triggers=ON")  # INSERT OR REPLACE must hit events_no_delete
        _retry_locked(lambda: _ensure_wal(conn))
        _retry_locked(lambda: migrate(conn))
    except BaseException:
        conn.close()
        raise
    return conn


def _ensure_wal(conn: sqlite3.Connection) -> None:
    # switching the journal mode does not wait on the busy handler, so only switch when needed
    if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() not in ("wal", "memory"):
        conn.execute("PRAGMA journal_mode=WAL")


def _retry_locked(fn, attempts: int = 50, delay: float = 0.1):
    """Run fn, retrying while another process holds the lock (e.g. two first-time connects)."""
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if ("locked" not in str(e) and "busy" not in str(e)) or i == attempts - 1:
                raise
            time.sleep(delay)


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transaction that nests (savepoints); rolls back everything on any exception."""
    if conn.in_transaction:
        name = f"sp{next(_sp_counter)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations in order; returns the resulting schema version."""
    current = schema_version(conn)
    for version, script in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{script}\n"
                f"INSERT INTO schema_version(version, applied_at) VALUES ({version}, '{now_iso()}');\n"
                "COMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if schema_version(conn) >= version:  # another process applied it while we waited
                current = schema_version(conn)
                continue
            raise
        current = version
    return current


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Row -> dict with JSON columns decoded."""
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLUMNS.intersection(d):
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


# ---------------------------------------------------------------- events

def log_event(conn: sqlite3.Connection, actor: str, action: str, job_id: str | None = None,
              **detail: Any) -> int:
    """Append an audit event; returns its id."""
    cur = conn.execute(
        "INSERT INTO events(ts, job_id, actor, action, detail_json) VALUES (?,?,?,?,?)",
        (now_iso(), job_id, actor, action, _dump(detail)))
    return cur.lastrowid


def list_events(conn: sqlite3.Connection, job_id: str | None = None, action: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if job_id is not None:
        sql += " AND job_id = ?"
        args.append(job_id)
    if action is not None:
        sql += " AND action = ?"
        args.append(action)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


# ---------------------------------------------------------------- jobs

_MUTABLE = [
    "source_url", "apply_url", "ats_type", "ats_slug", "company", "company_key", "title", "location",
    "country", "remote_type", "location_tier", "posted_at", "salary_min", "salary_max", "salary_currency",
    "salary_period", "exp_min", "exp_max", "jd_hash", "department", "employment_type", "channel",
]


def _job_values(job: Job) -> dict[str, Any]:
    return {
        "source": job.source, "source_job_id": job.source_job_id, "source_url": job.source_url,
        "apply_url": job.apply_url, "ats_type": job.ats_type, "ats_slug": job.ats_slug,
        "company": job.company, "company_key": company_key(job.company), "title": job.title,
        "location": job.location, "country": job.country, "remote_type": str(job.remote_type),
        "remote_scope": list(getattr(job, "remote_scope", None) or []),
        "location_tier": getattr(job, "location_tier", None), "posted_at": _iso(job.posted_at),
        "salary_min": job.salary_min, "salary_max": job.salary_max, "salary_currency": job.salary_currency,
        "salary_period": job.salary_period, "exp_min": job.exp_min, "exp_max": job.exp_max,
        "jd_text": job.jd_text or "", "jd_hash": job.jd_hash if _has_text(job.jd_text) else None, "department": job.department,
        "employment_type": job.employment_type, "channel": str(job.channel),
    }


def _has_text(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _merge_list(old: list, new: list) -> list:
    out = list(old)
    out.extend(x for x in new if x not in out)
    return out


def upsert_job(conn: sqlite3.Connection, job: Job, canonical_job_id: str | None = None) -> str:
    """Insert or refresh a job. Never touches status/notes; merges discovered_via. Returns job_id."""
    jid, ts, v = job.job_id, now_iso(), _job_values(job)
    with tx(conn):
        old = row_to_dict(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (jid,)).fetchone())
        if old is None:
            v.update(job_id=jid, canonical_job_id=canonical_job_id, discovered_via=_dump([job.source]),
                     remote_scope=_dump(v["remote_scope"]), first_seen=ts, last_seen=ts, is_live=1)
            cols = list(v)
            conn.execute(f"INSERT INTO jobs({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [v[c] for c in cols])
            return jid
        # a refresh never blanks a known value: None or "" means "not provided"
        upd = {c: (v[c] if v[c] is not None and v[c] != "" else old[c]) for c in _MUTABLE}
        if _has_text(v["jd_text"]):
            upd["jd_text"], upd["jd_hash"] = v["jd_text"], v["jd_hash"]
        else:  # no JD this time: keep the stored text and the hash that belongs to it
            upd["jd_text"], upd["jd_hash"] = old["jd_text"], old["jd_hash"]
        upd["remote_scope"] = _dump(_merge_list(old["remote_scope"] or [], v["remote_scope"]))
        upd["discovered_via"] = _dump(_merge_list(old["discovered_via"] or [], [job.source]))
        upd["canonical_job_id"] = canonical_job_id or old["canonical_job_id"]
        upd["last_seen"], upd["is_live"] = ts, 1
        conn.execute(f"UPDATE jobs SET {', '.join(f'{c} = ?' for c in upd)} WHERE job_id = ?",
                     [*upd.values(), jid])
    return jid


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    return row_to_dict(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())


_FILTERABLE = {"job_id", "canonical_job_id", "source", "company_key", "channel", "status", "is_live",
               "location_tier", "ats_type", "country", "remote_type"}


def list_jobs(conn: sqlite3.Connection, limit: int | None = None, **filters: Any) -> list[dict]:
    """Jobs matching column filters (scalar = equality, list/tuple/set = IN). None values are ignored."""
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    for col, val in filters.items():
        if col not in _FILTERABLE:
            raise ValueError(f"cannot filter jobs by {col!r}")
        if val is None:
            continue
        if isinstance(val, (list, tuple, set)):
            vals = list(val)
            sql += f" AND {col} IN ({','.join('?' * len(vals))})" if vals else " AND 0"
            args.extend(vals)
        else:
            sql += f" AND {col} = ?"
            args.append(val)
    sql += " ORDER BY COALESCE(location_tier, 9), first_seen DESC, job_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [row_to_dict(r) for r in conn.execute(sql, args)]


def set_status(conn: sqlite3.Connection, job_id: str, status: str, actor: str, note: str | None = None) -> bool:
    """Change a job's tracker status and log it. Returns False when it was already that status."""
    if status not in TRACKER_STATUSES:
        raise ValueError(f"invalid status {status!r}; expected one of {', '.join(TRACKER_STATUSES)}")
    with tx(conn):
        row = conn.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown job_id {job_id!r}")
        if row["status"] == status:
            return False
        conn.execute("UPDATE jobs SET status = ?, status_updated_at = ?, status_updated_by = ? WHERE job_id = ?",
                     (status, now_iso(), actor, job_id))
        detail = {"from": row["status"], "to": status}
        if note:
            detail["note"] = note
        log_event(conn, actor, "status_changed", job_id, **detail)
    return True


def set_notes(conn: sqlite3.Connection, job_id: str, notes: str | None, actor: str) -> bool:
    """Replace a job's notes and log it. Returns False when unchanged."""
    notes = notes or None
    with tx(conn):
        row = conn.execute("SELECT notes FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown job_id {job_id!r}")
        if (row["notes"] or None) == notes:
            return False
        conn.execute("UPDATE jobs SET notes = ? WHERE job_id = ?", (notes, job_id))
        log_event(conn, actor, "notes_changed", job_id, **{"from": row["notes"], "to": notes})
    return True


# ---------------------------------------------------------------- scores / companies / health

def add_score(conn: sqlite3.Connection, job_id: str, fit_score: float | None, band: str | None,
              **fields: Any) -> int:
    """Append a score row (the latest row per job is the current score)."""
    allowed = {"heuristic", "gates_json", "judgment_json", "confidence", "flags", "rubric_version",
               "skill_hash", "packet_hash"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown score fields: {sorted(bad)}")
    vals = {k: (_dump(v) if k in JSON_COLUMNS and v is not None else v) for k, v in fields.items()}
    vals.update(job_id=job_id, fit_score=fit_score, band=band, created_at=now_iso())
    with tx(conn):
        cur = conn.execute(f"INSERT INTO scores({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                           list(vals.values()))
    return cur.lastrowid


def upsert_company(conn: sqlite3.Connection, key: str, name: str, **fields: Any) -> None:
    """Insert or update a company row by company_key (only the given fields change)."""
    allowed = {"domain", "aliases", "verdict", "bond_flag", "fraud_flag", "employer_identified",
               "ratings_json", "reasons_json", "sources_json", "decided_by", "checked_at"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown company fields: {sorted(bad)}")
    vals = {k: (_dump(v) if k in JSON_COLUMNS and v is not None else v) for k, v in fields.items()}
    vals.update(company_key=key, name=name)
    sets = ", ".join(f"{c} = excluded.{c}" for c in vals if c != "company_key")
    with tx(conn):
        conn.execute(f"INSERT INTO companies({','.join(vals)}) VALUES ({','.join('?' * len(vals))}) "
                     f"ON CONFLICT(company_key) DO UPDATE SET {sets}", list(vals.values()))


def record_source_health(conn: sqlite3.Connection, run_id: str, source: str, ok: bool, n_jobs: int,
                         error: str | None = None) -> None:
    with tx(conn):
        conn.execute("INSERT INTO source_health(run_id, source, ok, n_jobs, error, ts) VALUES (?,?,?,?,?,?)",
                     (run_id, source, int(bool(ok)), int(n_jobs), error, now_iso()))


# ---------------------------------------------------------------- summary

def _count(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {(r[0] if r[0] is not None else "none"): r[1] for r in conn.execute(sql)}


def status_counts(conn: sqlite3.Connection) -> dict:
    """Counts by tracker status, source, channel and latest-score band."""
    latest = ("LEFT JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE job_id = j.job_id)")
    return {
        "total": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "by_status": _count(conn, "SELECT status, COUNT(*) FROM jobs GROUP BY 1 ORDER BY 2 DESC"),
        "by_source": _count(conn, "SELECT source, COUNT(*) FROM jobs GROUP BY 1 ORDER BY 2 DESC"),
        "by_channel": _count(conn, "SELECT channel, COUNT(*) FROM jobs GROUP BY 1 ORDER BY 2 DESC"),
        "by_band": _count(conn, f"SELECT COALESCE(s.band, 'unscored'), COUNT(*) FROM jobs j {latest} "
                                "GROUP BY 1 ORDER BY 2 DESC"),
    }
