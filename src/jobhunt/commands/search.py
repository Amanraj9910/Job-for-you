"""jobhunt search ..."""
from __future__ import annotations

from pathlib import Path

import typer

from .. import db
from ..util.out import emit, fail

app = typer.Typer(no_args_is_help=True, help="Find jobs across all sources and store them.")
JSON = typer.Option(False, "--json")


@app.command("run")
def run_cmd(sources: str = typer.Option(None, "--sources", help="Comma list (default: all enabled)"),
            query: str = typer.Option(None, "--query", help="Single query instead of the search.queries tiers in preferences.yaml"),
            location: str = typer.Option(None, "--location"),
            tiers: str = typer.Option("1,2", "--tiers", help="Query tiers from preferences.yaml (search.queries)"),
            hours_old: int = typer.Option(None, "--hours-old", help="Default: search.posted_within_days"),
            limit: int = typer.Option(None, "--limit", help="Max jobs per source call (default: profile)"),
            json_: bool = JSON) -> None:
    """Run the job search: fetch, normalize, store, dedupe, record source health."""
    from ..search import build_context, run
    conn = db.connect()
    ctx = build_context(conn, tiers=tuple(int(t) for t in tiers.split(",") if t.strip()), query=query,
                        location=location, hours_old=hours_old, limit=limit)
    names = [s.strip() for s in sources.split(",")] if sources else None
    res = run(conn, names, ctx)
    lines = [f"run {res['run_id']}: {res['new_jobs']} new / {res['seen_jobs']} seen; "
             f"duplicates merged {res['dedupe']['duplicates']}"]
    for n, s in res["sources"].items():
        lines.append(f"  {'OK ' if s.get('ok') else 'ERR'} {n:<18} jobs={s.get('jobs', 0):<4} new={s.get('new', 0):<4} "
                     f"{'; '.join(s.get('errors') or [])[:120] if s.get('errors') else s.get('error', '')}")
    emit(res, json_, "\n".join(lines))


@app.command("sources")
def sources_cmd(json_: bool = JSON) -> None:
    """List registered sources and which ones your profile enables."""
    from ..search import enabled_sources, load_sources
    reg = load_sources()
    on = set(enabled_sources())
    rows = [{"name": n, "enabled": n in on} for n in sorted(reg)]
    emit(rows, json_, "\n".join(f"{r['name']}{' (enabled)' if r['enabled'] else ''}" for r in rows))


@app.command("health")
def health_cmd(last: int = typer.Option(3, help="Runs to show"), json_: bool = JSON) -> None:
    """Source health for the last N runs (silent zero-result sources stand out)."""
    conn = db.connect()
    runs = [r[0] for r in conn.execute("SELECT DISTINCT run_id FROM source_health ORDER BY ts DESC LIMIT ?",
                                       (last,))]
    rows = [dict(r) for r in conn.execute(
        f"SELECT run_id, source, ok, n_jobs, error, ts FROM source_health WHERE run_id IN "
        f"({','.join('?' * len(runs))}) ORDER BY ts DESC", runs)] if runs else []
    emit(rows, json_, "\n".join(f"{r['run_id']} {r['source']:<18} ok={r['ok']} n={r['n_jobs']} {r['error'] or ''}"[:200]
                               for r in rows) or "no runs yet")


@app.command("hn-fetch")
def hn_fetch_cmd(month: str = typer.Option(None, "--month", help="e.g. 2026-11 (default: latest thread)"),
                 json_: bool = JSON) -> None:
    """Step 1 of the HN 'Who is hiring?' source: save relevant comments to a JSON file for your agent to read."""
    from ..sources.hn import fetch_thread
    try:
        t = fetch_thread(month)
    except Exception as e:  # noqa: BLE001
        fail(f"hn fetch failed: {e}", as_json=json_)
    emit({"ok": True, "story_id": t.story_id, "title": t.title, "file": str(t.path),
          "top_level": t.total_top_level, "kept": len(t.comments)}, json_,
         f"{t.title}: kept {len(t.comments)}/{t.total_top_level} comments -> {t.path}\n"
         f"Next: extract jobs from that file into a JSON list, then `jobhunt search ingest --source hn --file <it>`")


@app.command("ingest")
def ingest_cmd(source: str = typer.Option(..., "--source", help="hn (agent-extracted JSON)"),
               file: Path = typer.Option(..., "--file"), json_: bool = JSON) -> None:
    """Ingest jobs your agent extracted (e.g. from HN 'Who is hiring' comments)."""
    from ..dedupe import assign_canonical
    if source != "hn":
        fail("only --source hn is supported", as_json=json_)
    try:
        from ..sources.hn import ingest_file
    except ImportError as e:
        fail(f"hn source not available: {e}", as_json=json_)
    row_errors: list[str] = []
    jobs = ingest_file(file, row_errors)
    conn = db.connect()
    ids = [db.upsert_job(conn, j) for j in jobs]
    dd = assign_canonical(conn, ids) if ids else {}
    db.record_source_health(conn, "ingest-" + db.now_iso(), "hn", True, len(ids))
    emit({"ok": True, "ingested": len(ids), "dedupe": dd, "rejected_rows": row_errors}, json_,
         f"ingested {len(ids)} jobs" + (f"; {len(row_errors)} rows rejected: {row_errors[:5]}" if row_errors else ""))


@app.command("jobs")
def jobs_cmd(status: str = typer.Option(None), channel: str = typer.Option(None), limit: int = 50,
             json_: bool = JSON) -> None:
    """List stored jobs (filters: status, channel)."""
    conn = db.connect()
    rows = db.list_jobs(conn, limit=limit, status=status, channel=channel)
    slim = [{k: r.get(k) for k in ("job_id", "title", "company", "location", "location_tier", "channel", "status",
                                   "apply_url", "source")} for r in rows]
    emit(slim, json_, "\n".join(f"{r['job_id']} [{r['channel']}] {r['title']} @ {r['company']} — {r['location']}"
                                for r in slim) or "none")


@app.command("show")
def show_cmd(job: str = typer.Option(..., "--job"), json_: bool = JSON) -> None:
    """Show one job including its JD, latest score and duplicate group."""
    from ..dedupe import group_of
    from ..jobrow import latest_score
    conn = db.connect()
    row = db.get_job(conn, job)
    if not row:
        fail(f"unknown job {job}", as_json=json_)
    out = {**row, "score": latest_score(conn, job), "group": group_of(conn, job)}
    emit(out, True)
