"""Helpers bridging DB rows and models."""
from __future__ import annotations

import json
import sqlite3

from .models import Channel, Job, RemoteType


def job_from_row(row: dict) -> Job:
    def lst(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return []
        return v or []
    return Job(
        source=row["source"], source_job_id=row["source_job_id"], title=row["title"], company=row["company"],
        source_url=row.get("source_url"), apply_url=row.get("apply_url"), ats_type=row.get("ats_type"),
        ats_slug=row.get("ats_slug"), location=row.get("location"), country=row.get("country"),
        remote_type=RemoteType(row.get("remote_type") or "unknown"), remote_scope=lst(row.get("remote_scope")),
        location_tier=row.get("location_tier"), posted_at=row.get("posted_at"),
        salary_min=row.get("salary_min"), salary_max=row.get("salary_max"),
        salary_currency=row.get("salary_currency"), salary_period=row.get("salary_period"),
        exp_min=row.get("exp_min"), exp_max=row.get("exp_max"), jd_text=row.get("jd_text") or "",
        department=row.get("department"), employment_type=row.get("employment_type"),
        channel=Channel(row.get("channel") or "manual_other"),
    )


def latest_score(conn: sqlite3.Connection, job_id: str, judged_only: bool = False) -> dict | None:
    sql = "SELECT * FROM scores WHERE job_id = ?" + (" AND judgment_json IS NOT NULL" if judged_only else "") + \
          " ORDER BY id DESC LIMIT 1"
    r = conn.execute(sql, (job_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    for k in ("gates_json", "judgment_json", "flags"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d
