"""jobhunt vet ... — company vetting (your coding agent researches the web, code applies the rules)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from .. import db
from ..models import company_key
from ..profile import preferences
from ..util.out import emit, fail
from ..vet.redflags import scan
from ..vet.verdict import Verdict, apply_rules, research_packet

app = typer.Typer(no_args_is_help=True, help="Company vetting: fake jobs, fees, bonds, unpaid salaries, ratings.")
JSON = typer.Option(False, "--json")


def _cfg() -> dict:
    return preferences().get("vetting", {})


def _company_jobs(conn, key: str) -> list[dict]:
    return db.list_jobs(conn, company_key=key)


def _jd_flags(jobs: list[dict]) -> list[dict]:
    seen, flags = set(), []
    for j in jobs:
        for f in scan(j.get("jd_text") or ""):
            if f["flag"] not in seen:
                seen.add(f["flag"])
                flags.append({**f, "job_id": j["job_id"]})
    return flags


def _fresh(row: dict | None, days: int) -> bool:
    if not row or not row.get("checked_at") or not row.get("verdict"):
        return False
    try:
        t = datetime.fromisoformat(row["checked_at"])
    except ValueError:
        return False
    return datetime.now(UTC) - t < timedelta(days=days)


@app.command("pending")
def pending_cmd(min_fit: float = 80.0, json_: bool = JSON) -> None:
    """Companies of jobs with fit >= min_fit (or band apply/review) that have no fresh verdict."""
    conn = db.connect()
    days = _cfg().get("cache_days", 90)
    rows = conn.execute("""
        SELECT DISTINCT j.company_key, j.company FROM jobs j
        JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE job_id = j.job_id)
        WHERE (s.fit_score >= ? OR s.band IN ('apply','review')) AND j.is_live = 1""", (min_fit,)).fetchall()
    out = []
    for r in rows:
        comp = db.row_to_dict(conn.execute("SELECT * FROM companies WHERE company_key = ?",
                                           (r["company_key"],)).fetchone())
        if not _fresh(comp, days) and not (comp and comp.get("decided_by") == "user"):
            out.append({"company_key": r["company_key"], "company": r["company"]})
    emit(out, json_, "\n".join(f"{c['company']} ({c['company_key']})" for c in out) or "nothing to vet")


@app.command("packet")
def packet_cmd(company: str = typer.Option(..., "--company"), json_: bool = JSON) -> None:
    """Research packet for your agent: web queries, JD red flags, rules."""
    conn = db.connect()
    key = company_key(company)
    jobs = _company_jobs(conn, key)
    name = jobs[0]["company"] if jobs else company
    flags = _jd_flags(jobs)
    jd = jobs[0]["jd_text"] if jobs else ""
    p = research_packet(name, None, jd, flags, jobs[0]["apply_url"] if jobs else None, _cfg())
    p["job_ids"] = [j["job_id"] for j in jobs]
    emit(p, True)


@app.command("schema")
def schema_cmd() -> None:
    """JSON schema for the verdict the agent writes."""
    emit(Verdict.model_json_schema(), True)


@app.command("ingest")
def ingest_cmd(file: Path = typer.Option(..., "--file"), json_: bool = JSON) -> None:
    """Validate the agent's verdict JSON, apply deterministic rules, store the company verdict."""
    conn = db.connect()
    try:
        v = Verdict.model_validate_json(file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        fail(f"invalid verdict: {e}", as_json=json_)
    key = company_key(v.company)
    flags = _jd_flags(_company_jobs(conn, key))
    final, notes = apply_rules(v, flags, _cfg())
    db.upsert_company(conn, key, v.company, domain=v.domain, verdict=final, bond_flag=int(v.bond_flag),
                      fraud_flag=int(v.fraud_flag or v.unpaid_salary_flag),
                      employer_identified=int(v.employer_identified),
                      ratings_json=[r.model_dump(mode="json") for r in v.ratings],
                      reasons_json=[r.model_dump(mode="json") for r in v.reasons] + [{"text": n, "kind": "rule"}
                                                                                    for n in notes],
                      sources_json=[str(r.url) for r in v.reasons if r.url], decided_by="rules",
                      checked_at=db.now_iso())
    db.log_event(conn, "agent", "company_vetted", None, company=v.company, verdict=final, notes=notes,
                 agent_verdict=v.verdict)
    emit({"ok": True, "company": v.company, "company_key": key, "verdict": final, "notes": notes,
          "jd_flags": flags}, json_, f"{v.company}: {final}  {'; '.join(notes)}")


@app.command("decide")
def decide_cmd(company: str = typer.Option(..., "--company"),
               verdict: str = typer.Argument(..., help="ok | reject"),
               quote: str = typer.Option("", "--quote", help="Your reason, for the audit log"),
               json_: bool = JSON) -> None:
    """Record your own decision on a 'caution' company."""
    if verdict not in ("ok", "reject"):
        fail("verdict must be ok or reject", as_json=json_)
    conn = db.connect()
    key = company_key(company)
    row = db.row_to_dict(conn.execute("SELECT name FROM companies WHERE company_key = ?", (key,)).fetchone())
    db.upsert_company(conn, key, (row or {}).get("name") or company, verdict=verdict, decided_by="user",
                      checked_at=db.now_iso())
    db.log_event(conn, "user", "company_decided", None, company=company, verdict=verdict, quote=quote)
    emit({"ok": True, "company_key": key, "verdict": verdict}, json_, f"{company}: {verdict} (your decision)")


@app.command("show")
def show_cmd(company: str = typer.Option(..., "--company"), json_: bool = JSON) -> None:
    """Show the stored verdict, ratings, reasons and sources for one company."""
    conn = db.connect()
    row = db.row_to_dict(conn.execute("SELECT * FROM companies WHERE company_key = ?",
                                      (company_key(company),)).fetchone())
    emit(row or {}, json_, json.dumps(row or {}, indent=1, ensure_ascii=False))
