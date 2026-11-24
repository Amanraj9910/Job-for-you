"""jobhunt match ... — gates + heuristic (code), judge packets for the coding agent, judgment ingest + scoring (code)."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from .. import db
from ..jobrow import job_from_row, latest_score
from ..match.gates import run_gates, salary_ok
from ..match.heuristic import heuristic
from ..match.packet import build_packet
from ..match.score import Judgment, check_evidence, compute_score, judgment_json_schema
from ..profile import preferences
from ..resume import load_resume
from ..util.out import emit, fail

app = typer.Typer(no_args_is_help=True, help="Fit scoring: gates + heuristic (code), judge packets for your coding agent, final scores (code).")
JSON = typer.Option(False, "--json")
SHORTLIST_H = 55.0


def _applied_keys(conn) -> set[tuple]:
    rows = conn.execute("SELECT company_key, title FROM jobs WHERE status IN "
                        "('applied','interview','offer')").fetchall()
    return {(r[0], (r[1] or "").lower()) for r in rows}


@app.command("run")
def run_cmd(limit: int = typer.Option(500, help="Max jobs to pre-score"),
            top: int = typer.Option(30, help="Always shortlist at least this many best H"),
            rescore: bool = typer.Option(False, help="Re-run gates/heuristic for already scored jobs"),
            json_: bool = JSON) -> None:
    """Run gates + heuristic over unscored live jobs; print the shortlist the agent should judge."""
    conn = db.connect()
    m, prefs = load_resume(), preferences()
    excluded = {c.lower() for c in prefs.get("exclusions", {}).get("companies", [])}
    applied = _applied_keys(conn)
    rows = [r for r in db.list_jobs(conn, is_live=1) if not r.get("canonical_job_id") or
            r["canonical_job_id"] == r["job_id"]]
    done = skipped = 0
    scored: list[tuple[float, str]] = []
    for r in rows:
        if done >= limit:
            break
        if not rescore and latest_score(conn, r["job_id"]) is not None:
            continue
        job = job_from_row(r)
        g = run_gates(job, prefs, excluded_companies=excluded,
                      already_applied=(r["company_key"], r["title"].lower()) in applied)
        h = heuristic(job, m, prefs)
        band = "skip" if g.skip else None
        db.add_score(conn, r["job_id"], None, band, heuristic=h["h"], gates_json=g.as_dict(),
                     flags=g.flags, rubric_version="pre")
        done += 1
        if g.skip:
            skipped += 1
            if r["status"] == "new":
                db.set_status(conn, r["job_id"], "skipped", "jobhunt", note="; ".join(g.reasons)[:300])
        else:
            scored.append((h["h"], r["job_id"]))
    scored.sort(reverse=True)
    res = {"prescored": done, "gate_skipped": skipped, "shortlist": pending_list(conn, top)}
    emit(res, json_, f"pre-scored {done}, gate-skipped {skipped}, shortlist {len(res['shortlist'])}")


def pending_list(conn, top: int = 30) -> list[dict]:
    """Jobs with a pre-score but no judgment yet, H >= threshold or within the top N."""
    rows = conn.execute("""
        SELECT j.job_id, j.title, j.company, j.location, j.channel, s.heuristic
        FROM jobs j JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE job_id = j.job_id)
        WHERE s.judgment_json IS NULL AND COALESCE(s.band,'') != 'skip' AND j.is_live = 1
        ORDER BY s.heuristic DESC""").fetchall()
    out = []
    for i, r in enumerate(rows):
        if r["heuristic"] is not None and (r["heuristic"] >= SHORTLIST_H or i < top):
            out.append(dict(r))
    return out


@app.command("top")
def top_cmd(limit: int = typer.Option(20, "--limit"), min_fit: float = typer.Option(0, "--min-fit"),
            json_: bool = JSON) -> None:
    """Best judged jobs by fit score, with company verdict and link (excludes gate-skipped and rejected)."""
    conn = db.connect()
    rows = conn.execute("""
        SELECT j.job_id, j.title, j.company, j.location, j.status, COALESCE(j.apply_url, j.source_url) AS url,
               s.fit_score, s.band, c.verdict
        FROM jobs j JOIN scores s ON s.id = (SELECT MAX(id) FROM scores WHERE job_id = j.job_id)
        LEFT JOIN companies c ON c.company_key = j.company_key
        WHERE s.fit_score IS NOT NULL AND s.fit_score >= ? AND COALESCE(s.band,'') != 'skip'
              AND COALESCE(c.verdict,'') != 'reject' AND j.is_live = 1
        ORDER BY s.fit_score DESC LIMIT ?""", (min_fit, limit)).fetchall()
    out = [dict(r) for r in rows]
    emit(out, json_, "\n".join(f"{r['fit_score']:>5.1f} {r['band']:<6} {(r['verdict'] or 'not vetted'):<10} "
                               f"{r['title']} @ {r['company']} — {r['url']}" for r in out) or "no judged jobs yet")


@app.command("pending")
def pending_cmd(top: int = 30, json_: bool = JSON) -> None:
    """Shortlisted jobs still waiting for a judgment."""
    lst = pending_list(db.connect(), top)
    emit(lst, json_, "\n".join(f"{r['job_id']}  H={r['heuristic']:.0f}  {r['title']} @ {r['company']} "
                               f"[{r['channel']}]" for r in lst) or "nothing pending")


@app.command("packet")
def packet_cmd(job: str = typer.Option(..., "--job"), out: Path = typer.Option(None, "--out"),
               json_: bool = JSON) -> None:
    """Emit the judge packet for one job (your agent reads it and writes a judgment JSON)."""
    conn = db.connect()
    row = db.get_job(conn, job)
    if not row:
        fail(f"unknown job {job}", as_json=json_)
    j, m, prefs = job_from_row(row), load_resume(), preferences()
    sc = latest_score(conn, job)
    gates = (sc or {}).get("gates_json") or run_gates(j, prefs).as_dict()
    p = build_packet(j, job, m, prefs, None, gates, heuristic(j, m, prefs))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        emit({"ok": True, "packet": str(out), "packet_hash": p["packet_hash"]}, json_, str(out))
    else:
        emit(p, True)


@app.command("schema")
def schema_cmd() -> None:
    """Print the JSON schema the judgment must follow."""
    emit(judgment_json_schema(), True)


@app.command("ingest")
def ingest_cmd(job: str = typer.Option(..., "--job"), file: Path = typer.Option(..., "--file"),
               json_: bool = JSON) -> None:
    """Validate a judgment, check evidence, compute the fit score and band, store it."""
    conn = db.connect()
    row = db.get_job(conn, job)
    if not row:
        fail(f"unknown job {job}", as_json=json_)
    try:
        jdg = Judgment.model_validate_json(file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        fail(f"invalid judgment: {e}", as_json=json_)
    if jdg.job_id != job:
        fail(f"judgment is for {jdg.job_id}, not {job}", as_json=json_)
    m, prefs = load_resume(), preferences()
    notes = check_evidence(jdg, m)
    j = job_from_row(row)
    pre = latest_score(conn, job) or {}
    gates = pre.get("gates_json") or run_gates(j, prefs).as_dict()
    res = compute_score(jdg, location_tier=j.location_tier, remote=j.remote_type.value == "remote",
                        salary_ok=salary_ok(j, prefs), gate_cap=gates.get("cap"),
                        gate_band_cap=gates.get("band_cap"), gate_skip=gates.get("skip", False))
    from ..match.packet import build_packet as _bp
    packet_hash = _bp(j, job, m, prefs, None, gates, heuristic(j, m, prefs))["packet_hash"]
    db.add_score(conn, job, res["fit_score"], res["band"], heuristic=pre.get("heuristic"), gates_json=gates,
                 judgment_json={**jdg.model_dump(), "evidence_notes": notes, "score": res},
                 confidence=jdg.confidence, flags=gates.get("flags", []), rubric_version=res["rubric_version"],
                 packet_hash=packet_hash)
    new_status = {"apply": "shortlisted", "review": "review"}.get(res["band"])
    if row["status"] in ("new", "skipped") and new_status:
        db.set_status(conn, job, new_status, "jobhunt", note=f"fit {res['fit_score']}")
    elif row["status"] == "new" and res["band"] == "skip":
        db.set_status(conn, job, "skipped", "jobhunt", note=f"fit {res['fit_score']}")
    db.log_event(conn, "agent", "judged", job, fit=res["fit_score"], band=res["band"], notes=notes)
    out = {"ok": True, "job_id": job, **res, "evidence_notes": notes}
    emit(out, json_, f"{job}: fit {res['fit_score']} band {res['band']} ({jdg.confidence})")
