"""Cross-source dedupe: pick a canonical job for postings that are the same role (A14).

Same role = same company_key and either the same normalized title + overlapping location, or a fuzzy title
match >= 90 first seen within 30 days of each other. Preference for the canonical copy:
ATS (auto) > employer site (auto_probe) > account portal > boards/LinkedIn/Naukri; then richer JD; then oldest.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from .models import title_key

_CHANNEL_RANK = {"auto": 0, "auto_probe": 1, "manual_account_portal": 2, "manual_email": 3, "manual_other": 4,
                 "manual_linkedin_naukri": 5}


def _loc_tokens(loc: str | None) -> set[str]:
    from .normalize import find_home_city
    city = find_home_city(loc)
    toks = {city.lower()} if city else set()
    if loc and "remote" in loc.lower():
        toks.add("remote")
    return toks


def same_role(a: dict, b: dict, window_days: int = 30) -> bool:
    if a["company_key"] != b["company_key"]:
        return False
    ta, tb = title_key(a["title"]), title_key(b["title"])
    la, lb = _loc_tokens(a.get("location")), _loc_tokens(b.get("location"))
    loc_ok = not la or not lb or bool(la & lb)
    if ta == tb and loc_ok:
        return True
    if fuzz.token_sort_ratio(ta, tb) >= 90 and loc_ok:
        try:
            da = datetime.fromisoformat(a["first_seen"])
            db_ = datetime.fromisoformat(b["first_seen"])
            return abs(da - db_) <= timedelta(days=window_days)
        except (KeyError, TypeError, ValueError):
            return True
    return False


def rank(j: dict) -> tuple:
    return (_CHANNEL_RANK.get(j.get("channel") or "manual_other", 9), -len(j.get("jd_text") or ""),
            j.get("first_seen") or "", j["job_id"])


def assign_canonical(conn: sqlite3.Connection, job_ids: list[str] | None = None) -> dict:
    """Recompute canonical_job_id within each company that has new/changed jobs. Returns stats."""
    if job_ids:
        q = f"SELECT DISTINCT company_key FROM jobs WHERE job_id IN ({','.join('?' * len(job_ids))})"
        companies = [r[0] for r in conn.execute(q, job_ids)]
    else:
        companies = [r[0] for r in conn.execute("SELECT DISTINCT company_key FROM jobs")]
    groups = merged = 0
    for ck in companies:
        rows = [dict(r) for r in conn.execute(
            "SELECT job_id, company_key, title, location, channel, jd_text, first_seen, canonical_job_id, status "
            "FROM jobs WHERE company_key = ? AND is_live = 1", (ck,))]
        clusters: list[list[dict]] = []
        for r in rows:
            for c in clusters:
                if any(same_role(r, x) for x in c):
                    c.append(r)
                    break
            else:
                clusters.append([r])
        for c in clusters:
            best = sorted(c, key=rank)[0]
            # a member already applied/queued keeps the group blocked: carry status info via canonical
            for r in c:
                if r["canonical_job_id"] != best["job_id"]:
                    conn.execute("UPDATE jobs SET canonical_job_id = ? WHERE job_id = ?", (best["job_id"], r["job_id"]))
            groups += 1
            merged += len(c) - 1
    conn.commit()
    return {"groups": groups, "duplicates": merged}


def group_of(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    r = conn.execute("SELECT canonical_job_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    canon = (r and r[0]) or job_id
    return [dict(x) for x in conn.execute(
        "SELECT job_id, source, channel, status, apply_url FROM jobs WHERE canonical_job_id = ? OR job_id = ?",
        (canon, canon))]
