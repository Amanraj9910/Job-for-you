"""jobhunt report ... — the HTML jobs list (output/jobs.html)."""
from __future__ import annotations

import webbrowser
from pathlib import Path

import typer

from ..util.out import emit, fail

app = typer.Typer(no_args_is_help=True, help="Your jobs list as one offline HTML page (filters, ticks, CSV export).")
JSON = typer.Option(False, "--json")


@app.command("build")
def build_cmd(out: Path = typer.Option(None, "--out", help="Default: output/jobs.html"),
              open_: bool = typer.Option(False, "--open", help="Open it in your browser"),
              json_: bool = JSON) -> None:
    """Build the jobs report from the database."""
    from ..report import build
    res = build(out)
    if open_:
        webbrowser.open(Path(res["out"]).resolve().as_uri())
    emit(res, json_, f"{res['rows']} jobs ({res['passing']} passing filters, {res['vetted_ok']} at vetted companies, "
                     f"{res['direct']} direct from employers) -> {res['out']}")


@app.command("sync")
def sync_cmd(file: Path = typer.Argument(..., help="applied_ticks.json saved from the report ('Save backup')"),
             json_: bool = JSON) -> None:
    """Write your Applied / Not interested ticks and notes back into the database, then rebuild."""
    from ..report import build, sync
    if not file.exists():
        fail(f"{file} not found", as_json=json_)
    res = sync(file)
    built = build()
    emit({"synced": res, **built}, json_, f"synced {res}; rebuilt {built['out']}")
