"""`jobhunt db ...` and top-level `jobhunt status`."""
from __future__ import annotations

import typer

from .. import db
from ..config import settings
from ..util.out import emit, fail

app = typer.Typer(no_args_is_help=True, help="SQLite store: create/migrate, manual status changes.")

JSON = typer.Option(False, "--json", help="Machine-readable JSON output")


@app.command()
def init(json_: bool = JSON) -> None:
    """Create the DB (or apply pending migrations)."""
    conn = db.connect()
    try:
        res = {"ok": True, "db": str(settings().db_path), "schema_version": db.schema_version(conn)}
    finally:
        conn.close()
    emit(res, json_, f"DB ready: {res['db']} (schema v{res['schema_version']})")


@app.command("set-status")
def set_status_cmd(
    job: str = typer.Option(..., "--job", help="job_id"),
    status: str = typer.Option(..., "--status", help=f"one of: {', '.join(db.TRACKER_STATUSES)}"),
    note: str | None = typer.Option(None, "--note", help="Reason, stored in the audit event"),
    json_: bool = JSON,
) -> None:
    """Set a job's tracker status (logged as an event)."""
    conn = db.connect()
    try:
        changed = db.set_status(conn, job, status, actor="cli", note=note)
    except (ValueError, KeyError) as e:
        fail(str(e).strip("'\""), 2, json_)
    finally:
        conn.close()
    emit({"ok": True, "job_id": job, "status": status, "changed": changed}, json_,
         f"{job}: {status}" + ("" if changed else " (unchanged)"))


def status_cmd(json_: bool = JSON) -> None:
    """Counts of jobs by status, source, channel and band."""
    conn = db.connect()
    try:
        res = db.status_counts(conn)
    finally:
        conn.close()
    if json_:
        emit(res, True)
        return
    typer.echo(f"jobs: {res['total']}")
    for key in ("by_status", "by_source", "by_channel", "by_band"):
        parts = ", ".join(f"{k}={v}" for k, v in res[key].items()) or "-"
        typer.echo(f"{key[3:]:>8}: {parts}")


TOP_LEVEL = [status_cmd]
