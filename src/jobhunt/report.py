"""The jobs report: one self-contained HTML page listing your matches (the standard job listing).

    jobhunt report build                 # -> output/jobs.html (open it in any browser, works offline)
    jobhunt report build --open          # ...and open it
    jobhunt report sync ~/Downloads/applied_ticks.json   # write your ticks/notes back into the DB

What is listed: live, de-duplicated jobs whose title fits your profile (title_keywords), located in your home
country or remote, posted within ``report.posted_within_days``. Each row shows the fit score, a **Trust** column
(company vetting verdict, "Direct" when the posting comes from the employer's own ATS, job-description red flags),
location, work type, pay, experience and the link. Ticks (Applied / Not interested) and notes are kept in the
browser and can be saved as a backup JSON and synced back with ``report sync``.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

from . import db as jdb
from .config import settings
from .geo import current, is_home
from .profile import preferences, title_keywords

DIRECT_SOURCES = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "bamboohr", "breezy",
                  "keka", "zohorecruit", "workday"}
APPLIED_STATUSES = {"applied", "interview", "offer", "rejected"}

SAL_RX = re.compile(
    r"((?:₹|INR|Rs\.?|\$|USD|US\$|€|EUR|£|GBP)\s?\d[\d,.]*\s?(?:k|K|L|LPA|lakhs?|Lakhs?|M|Cr)?"
    r"(?:\s?(?:-|–|to)\s?(?:₹|INR|Rs\.?|\$|USD|€|EUR|£|GBP)?\s?\d[\d,.]*\s?(?:k|K|L|LPA|lakhs?|M|Cr)?)?"
    r"(?:\s?(?:per annum|/yr|/year|per year|a year|annually|/month|per month|/hr|per hour|LPA))?"
    r"|\d[\d.]*\s?(?:-|–|to)\s?\d[\d.]*\s?(?:LPA|lakhs?(?: per annum)?|L\.?P\.?A))", re.I)
SAL_CTX = re.compile(r"(salary|compensation|pay\b|pay range|ctc|stipend|package|remuneration|per annum|lpa|"
                     r"rate|hourly|/hr|per hour|base)", re.I)
BRIEF_HINT = re.compile(r"\b(you will|you'll|we are looking|we're looking|responsib|this role|the role|in this role|"
                        r"seeking|looking for|will be responsible|you would|you'd)\b", re.I)
BRIEF_HINT2 = re.compile(r"\b(build|design|develop|implement|deploy|work on)\b", re.I)
NOISE = re.compile(r"(equal opportunity|eeo|accommodation|privacy|cookie|benefits include|about us\b)", re.I)


def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"[*#_`>|\\]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def brief(jd: str, limit: int = 320) -> str:
    t = clean(jd)
    sents = [s for s in re.split(r"(?<=[.!?])\s+", t) if 40 <= len(s) <= 400 and not NOISE.search(s)]
    pick = [s for s in sents if BRIEF_HINT.search(s)][:2] or [s for s in sents if BRIEF_HINT2.search(s)][:2] \
        or sents[:2]
    out = " ".join(pick).lstrip("-• ")
    return (out[: limit - 1].rsplit(" ", 1)[0] + "…") if len(out) > limit else out


def money(v: float, cur: str) -> str:
    cur = (cur or "").upper()
    if cur == "INR":
        return f"₹{v / 1e5:.1f} L" if v >= 1e5 else f"₹{v:,.0f}"
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$"}.get(cur, cur + " ")
    return f"{sym}{v / 1000:.0f}k" if v >= 10000 else f"{sym}{v:,.0f}"


def compensation(r) -> tuple[str, bool]:
    lo, hi, cur, per = r["salary_min"], r["salary_max"], r["salary_currency"], r["salary_period"]
    if lo or hi:
        p = (per or "").lower()
        top = max(x for x in (lo, hi) if x)
        if (cur or "").upper() in ("USD", "EUR", "GBP", "CAD", "AUD") and p.startswith("year") and top > 600_000:
            return "Listed value looks invalid", False
        rng = " – ".join(dict.fromkeys(money(x, cur) for x in (lo, hi) if x))
        suffix = {"year": "/yr", "yearly": "/yr", "month": "/mo", "monthly": "/mo", "hour": "/hr",
                  "hourly": "/hr"}.get(p, f"/{per}" if per else "")
        return rng + suffix, True
    text = clean(r["jd_text"])
    for m in SAL_RX.finditer(text):
        s = m.group(0).strip()
        tail = text[m.end(): m.end() + 9].lower()
        if not re.search(r"\d{2}", s) or re.search(r"\b(m|cr|million|billion|bn)\b", s.lower() + " " + tail):
            continue
        if SAL_CTX.search(text[max(0, m.start() - 90): m.end() + 25]):
            return s + " (JD)", True
    return "", False


def work_type(r, text: str) -> str:
    rt = (r["remote_type"] or "").lower()
    if rt in ("remote", "hybrid", "onsite"):
        return {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}[rt]
    if re.search(r"\bhybrid\b", text, re.I):
        return "Hybrid"
    if re.search(r"\b(remote|work from home|wfh)\b", text, re.I):
        return "Remote"
    return "Not stated"


def _rx(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words if w and w.strip()]
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b", re.I)


def title_filter():
    """-> fn(title) -> bool, from your profile: strong keyword, or related keyword + a role word; minus excludes."""
    tk = title_keywords()
    strong, related, roles = _rx(tk.get("strong") or []), _rx(tk.get("related") or []), _rx(tk.get("role_words") or [])
    exclude = _rx(tk.get("exclude") or [])

    def ok(title: str) -> bool:
        if exclude and exclude.search(title):
            return False
        if strong and strong.search(title):
            return True
        return bool(related and related.search(title) and (roles is None or roles.search(title)))
    return ok


def _red_flags(jd: str) -> str:
    from .vet.redflags import scan
    return "; ".join(f"{f['flag'].replace('_', ' ')}" for f in scan(jd or "")[:3])


def collect(conn: sqlite3.Connection, days: int | None = None, now: datetime | None = None) -> list[dict]:
    """Rows for the report (see module docstring for what is included)."""
    prefs = preferences()
    days = days or int((prefs.get("report") or {}).get("posted_within_days", 30))
    now = now or datetime.now(UTC)
    rows = conn.execute("""
        SELECT j.*, s.heuristic, s.gates_json, s.fit_score, c.verdict AS company_verdict
        FROM jobs j
        LEFT JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE job_id = j.job_id)
        LEFT JOIN companies c ON c.company_key = j.company_key
        WHERE j.is_live = 1 AND (j.canonical_job_id IS NULL OR j.canonical_job_id = j.job_id)
    """).fetchall()
    cutoff = now - timedelta(days=days)
    relevant = title_filter()
    out = []
    for r in rows:
        title = r["title"] or ""
        applied_db = r["status"] in APPLIED_STATUSES
        if not applied_db and not relevant(title):
            continue
        posted, seen = parse_dt(r["posted_at"]), parse_dt(r["first_seen"])
        when = posted or seen
        loc = r["location"] or ""
        home = is_home(loc, r["country"]) or (r["location_tier"] in (1, 2, 3))
        remote = (r["remote_type"] or "").lower() == "remote"
        if not applied_db:            # keep anything already applied to, regardless of age
            if not home and not remote:
                continue
            if when and when < cutoff:
                continue
        gates = json.loads(r["gates_json"]) if r["gates_json"] else {}
        text = clean(r["jd_text"])
        comp, has_comp = compensation(r)
        score = r["fit_score"] if r["fit_score"] is not None else r["heuristic"]
        et = (r["employment_type"] or "").replace("_", " ").replace("fulltime", "full-time").strip().title()
        exp = ""
        if r["exp_min"] is not None:
            exp = f"{r['exp_min']:g}" + (f"–{r['exp_max']:g}" if r["exp_max"] else "+")
        direct = (r["source"] in DIRECT_SOURCES) or (r["channel"] or "") in ("auto", "auto_probe")
        out.append({
            "id": r["job_id"], "region": "Home" if home else "Foreign", "title": title,
            "company": r["company"], "loc": loc or ("Remote" if remote else ""),
            "work": work_type(r, text), "emp": et, "comp": comp, "hc": int(has_comp), "exp": exp,
            "posted": (when.date().isoformat() if when else ""), "pe": int(posted is None),
            "score": round(score) if score is not None else None, "fit": int(r["fit_score"] is not None),
            "skip": int(bool(gates.get("skip"))), "why": "; ".join(gates.get("reasons", [])),
            "url": r["apply_url"] or r["source_url"] or "", "brief": brief(r["jd_text"]),
            "src": r["source"], "manual": int((r["channel"] or "").startswith("manual")),
            "vf": r["company_verdict"] or "", "direct": int(direct), "rf": _red_flags(r["jd_text"]),
            "st": r["status"], "upd": (r["status_updated_at"] or "")[:10], "notes": r["notes"] or "",
        })
    out.sort(key=lambda x: (x["skip"], x["vf"] == "reject", -(x["score"] or 0)))
    return out


def template() -> str:
    return resources.files("jobhunt").joinpath("static/report.html").read_text(encoding="utf-8")


def render(rows: list[dict], *, now: datetime | None = None, days: int | None = None) -> str:
    prefs = preferences()
    g = current()
    days = days or int((prefs.get("report") or {}).get("posted_within_days", 30))
    title = (prefs.get("report") or {}).get("title") or "My job matches"
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    gen = (now or datetime.now(UTC)).astimezone().strftime("%d %b %Y, %H:%M")
    scope = f"{g.home_name} roles + remote roles open to you ({', '.join(sorted(g.ok_scopes))})"
    esc = html.escape
    return (template().replace("__DATA__", data).replace("__GEN__", esc(gen)).replace("__DAYS__", str(days))
            .replace("__TITLE__", esc(title)).replace("__SCOPE__", esc(scope)).replace("__HOME__", esc(g.home_name)))


def build(out: Path | None = None, conn: sqlite3.Connection | None = None) -> dict:
    conn = conn or jdb.connect()
    rows = collect(conn)
    out = out or settings().report_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows), encoding="utf-8")
    return {"out": str(out), "rows": len(rows), "home": sum(r["region"] == "Home" for r in rows),
            "remote_abroad": sum(r["region"] == "Foreign" for r in rows),
            "passing": sum(not r["skip"] and r["vf"] != "reject" for r in rows),
            "vetted_ok": sum(r["vf"] == "ok" for r in rows), "direct": sum(r["direct"] for r in rows)}


def sync(path: Path, conn: sqlite3.Connection | None = None) -> dict:
    """Write ticks/notes from a saved ``applied_ticks.json`` into the DB (status applied / not_interested)."""
    conn = conn or jdb.connect()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = data.get("jobs", data)
    res = {"applied": 0, "not_interested": 0, "reset": 0, "notes": 0, "unknown": 0, "unchanged": 0}
    for jid, st in jobs.items():
        row = conn.execute("SELECT status, notes FROM jobs WHERE job_id = ?", (jid,)).fetchone()
        if row is None:
            res["unknown"] += 1
            continue
        cur = row["status"]
        target = key = None
        if st.get("applied"):
            if cur not in APPLIED_STATUSES:
                target, key = "applied", "applied"
        elif st.get("hidden"):
            if cur not in APPLIED_STATUSES and cur != "not_interested":
                target, key = "not_interested", "not_interested"
        elif cur in ("applied", "not_interested") and ("applied" in st or "hidden" in st):
            target, key = "new", "reset"      # unticked in the report
        if target:
            note = f"ticked in jobs report{(' on ' + st['on']) if st.get('on') else ''}"
            jdb.set_status(conn, jid, target, "report", note=note)
            res[key] += 1
        else:
            res["unchanged"] += 1
        note = (st.get("note") or "").strip()
        if note and note != (row["notes"] or ""):
            jdb.set_notes(conn, jid, note, "report")
            res["notes"] += 1
    return res
