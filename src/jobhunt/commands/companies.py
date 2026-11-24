"""jobhunt companies ... — watchlist of employer ATS boards (Greenhouse, Lever, Ashby, …) polled by search."""
from __future__ import annotations

import csv
from pathlib import Path

import typer

from .. import db
from ..config import settings
from ..util.out import emit, fail

app = typer.Typer(no_args_is_help=True, help="Company watchlist: employer job boards polled directly by search.")
JSON = typer.Option(False, "--json")

# Raw ATS tenant lists maintained by the open-source ats-scrapers project. Downloaded on request, never vendored.
UPSTREAM = "https://raw.githubusercontent.com/kalil0321/ats-scrapers"
UPSTREAM_SHA = "6b44a1badc9bfbf5cf176f75265cc5729e520e99"
UPSTREAM_ATS = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "keka", "workday"]


def seeds_dir() -> Path:
    return settings().data_dir / "companies" / "seeds"


@app.command("import-seeds")
def import_seeds(file: Path = typer.Option(None, "--file", help="CSV with company,ats,slug (default: seeds/india_ai.csv)"),
                 state: str = typer.Option("active", help="Initial state for imported boards"),
                 json_: bool = JSON) -> None:
    """Load a curated seed CSV (company,ats,slug) into the watchlist (idempotent)."""
    from ..search import add_to_watchlist
    path = file or seeds_dir() / "india_ai.csv"
    if not path.exists():
        fail(f"{path} not found", as_json=json_)
    conn = db.connect()
    added = skipped = 0
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ats, slug = (row.get("ats") or "").strip().lower(), (row.get("slug") or "").strip()
            if not ats or not slug:
                skipped += 1
                continue
            if add_to_watchlist(conn, ats, slug, row.get("company") or slug, origin=f"seed:{path.name}", state=state):
                added += 1
    emit({"ok": True, "added": added, "skipped": skipped, "file": str(path)}, json_,
         f"added {added} boards from {path} (skipped {skipped})")


@app.command("fetch-seeds")
def fetch_seeds(ats: str = typer.Option(",".join(UPSTREAM_ATS), help="Comma list of ATS names"),
                json_: bool = JSON) -> None:
    """Download raw company lists (tens of thousands of boards) from the ats-scrapers project (pinned commit).

    These are NOT imported directly: run `jobhunt companies probe --csv <file>` to keep only boards that have
    jobs matching your profile right now."""
    from ..util.http import http
    out = seeds_dir() / "upstream"
    out.mkdir(parents=True, exist_ok=True)
    got, errors = [], []
    for a in [x.strip() for x in ats.split(",") if x.strip()]:
        url = f"{UPSTREAM}/{UPSTREAM_SHA}/ats-companies/{a}.csv"
        try:
            r = http().get(url)
            r.raise_for_status()
            p = out / f"ats_scrapers_{a}.csv"
            p.write_text(r.text, encoding="utf-8")
            got.append({"ats": a, "file": str(p), "rows": max(0, r.text.count("\n") - 1)})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{a}: {e}")
    emit({"ok": not errors, "files": got, "errors": errors, "source": f"{UPSTREAM}@{UPSTREAM_SHA}"}, json_,
         "\n".join([f"{g['ats']:<16} {g['rows']:>6} rows  {g['file']}" for g in got] + errors))


@app.command("probe")
def probe_cmd(csv_path: Path = typer.Option(..., "--csv", help="Seed CSV (company,ats,slug or name,slug,url)"),
              ats: str = typer.Option(None, "--ats", help="ATS for CSVs without an ats column"),
              limit: int = typer.Option(50, "--limit", help="New boards to probe this run (resumable)"),
              add: bool = typer.Option(True, "--add/--no-add", help="Add boards with relevant openings"),
              json_: bool = JSON) -> None:
    """Probe boards from a CSV (~1 request/s per ATS) and add the ones with jobs matching your profile."""
    from ..search import add_to_watchlist
    from ..sources.discover import probe_many, read_seed_csv
    rows = read_seed_csv(csv_path, default_ats=ats)
    res = probe_many(rows, limit=limit, progress=not json_)
    conn = db.connect()
    added = []
    for r in res:
        if add and r.get("live") and (r.get("n_relevant") or 0) > 0:
            if add_to_watchlist(conn, r["ats"], r["slug"], r.get("company") or r.get("company_api") or r["slug"],
                                origin=f"probe:{csv_path.name}", state="active"):
                added.append(f"{r['ats']}:{r['slug']}")
    live = sum(1 for r in res if r.get("live"))
    rel = sum(1 for r in res if (r.get("n_relevant") or 0) > 0)
    emit({"probed": len(res), "live": live, "relevant": rel, "added": added}, json_,
         f"probed {len(res)} (cached + new), live {live}, with relevant openings {rel}, added {len(added)}")


@app.command("add")
def add_cmd(ats: str = typer.Option(...), slug: str = typer.Option(...), name: str = typer.Option(None),
            json_: bool = JSON) -> None:
    """Add one board, e.g. --ats greenhouse --slug postman."""
    from ..search import add_to_watchlist
    ok = add_to_watchlist(db.connect(), ats.lower(), slug, name, origin="manual", state="active")
    emit({"ok": True, "added": ok}, json_, "added" if ok else "already present")


@app.command("list")
def list_cmd(state: str = typer.Option(None), json_: bool = JSON) -> None:
    """Show the watchlist."""
    conn = db.connect()
    sql = "SELECT * FROM watchlist" + (" WHERE state = ?" if state else "") + " ORDER BY ats_type, ats_slug"
    rows = [dict(r) for r in conn.execute(sql, (state,) if state else ())]
    emit(rows, json_, "\n".join(f"{r['ats_type']:<16} {r['ats_slug']:<30} {r['state']:<8} n={r['n_jobs']}"
                                for r in rows) or "empty — run `jobhunt companies import-seeds` or `companies probe`")


@app.command("discover")
def discover_cmd(url: str = typer.Option(..., "--url", help="A company careers page"), json_: bool = JSON) -> None:
    """Find which ATS a careers page uses and add its board(s) to the watchlist."""
    from ..search import add_to_watchlist
    from ..sources.discover import career_page_ats
    found = career_page_ats(url)
    conn = db.connect()
    added = [f"{a}:{s}" for a, s in found if s and add_to_watchlist(conn, a, s, None, origin=f"discover:{url}")]
    emit({"found": found, "added": added}, json_, f"found {found}; added {added}")
