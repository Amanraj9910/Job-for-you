"""jobhunt command line. Every command supports --json, so any coding agent can drive it."""
from __future__ import annotations

import shutil
import sys

import typer

from . import __version__
from .util.logs import setup_logging
from .util.out import emit

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="JobHunt: find jobs that fit your resume, score them, vet the companies, list them in one page. "
                       "Runs locally; works with any coding agent (or none).")

JSON = typer.Option(False, "--json", help="Machine-readable JSON output")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging to stderr")) -> None:
    setup_logging(verbose)


@app.command()
def version(json_: bool = JSON) -> None:
    """Print version."""
    emit({"version": __version__}, json_, __version__)


@app.command()
def init(force: bool = typer.Option(False, "--force", help="Overwrite existing profile files with the examples"),
         seeds: bool = typer.Option(False, "--seeds", help="Also import the curated company seed list"),
         json_: bool = JSON) -> None:
    """First run: create profile/ from profile.example/, the database and output folders."""
    from . import db
    from .config import settings
    s = settings()
    s.ensure_dirs()
    copied = []
    for f in sorted(s.example_profile_dir.glob("*.yaml")):
        dst = s.profile_dir / f.name
        if force or not dst.exists():
            shutil.copy2(f, dst)
            copied.append(str(dst))
    conn = db.connect()
    added = 0
    if seeds:
        import csv

        from .commands.companies import seeds_dir
        from .search import add_to_watchlist
        with open(seeds_dir() / "india_ai.csv", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("ats") and row.get("slug") and add_to_watchlist(
                        conn, row["ats"].strip().lower(), row["slug"].strip(), row.get("company"),
                        origin="seed:india_ai.csv", state="active"):
                    added += 1
    nxt = ["Personalize your profile (pick one):",
           "  a) jobhunt profile form            # fill a form in your browser, then `jobhunt profile import ...`",
           "  b) ask your coding agent: \"set up my JobHunt profile from my resume\" (playbooks/setup.md)",
           "  c) edit profile/preferences.yaml and profile/resume.yaml by hand",
           "Then: jobhunt profile validate  ->  jobhunt search run  ->  jobhunt match run  ->  jobhunt report build --open"]
    emit({"ok": True, "profile_dir": str(s.profile_dir), "copied": copied, "db": str(s.db_path),
          "seed_boards_added": added, "next": nxt}, json_,
         "\n".join([f"profile: {s.profile_dir} ({len(copied)} example file(s) copied)", f"database: {s.db_path}",
                    *( [f"seed boards added: {added}"] if seeds else []), "", *nxt]))


@app.command()
def doctor(json_: bool = JSON) -> None:
    """Check the environment and your profile."""
    from .doctor import run_checks
    res = run_checks()
    if json_:
        emit(res, True)
    else:
        for c in res["checks"]:
            mark = "OK " if c["ok"] else ("FAIL" if c["required"] else "warn")
            typer.echo(f"[{mark}] {c['name']} {c['detail']}")
        typer.echo("ALL GOOD" if res["ok"] else "PROBLEMS FOUND")
    raise typer.Exit(0 if res["ok"] else 1)


def _register_subcommands() -> None:
    import importlib
    for mod, name in [
        ("jobhunt.commands.profile", "profile"),
        ("jobhunt.commands.db", "db"),
        ("jobhunt.commands.search", "search"),
        ("jobhunt.commands.companies", "companies"),
        ("jobhunt.commands.match", "match"),
        ("jobhunt.commands.vet", "vet"),
        ("jobhunt.commands.report", "report"),
    ]:
        m = importlib.import_module(mod)
        app.add_typer(m.app, name=name)
        for extra in getattr(m, "TOP_LEVEL", []):
            app.command(extra.__name__.replace("_cmd", "").replace("_", "-"))(extra)


_register_subcommands()

if __name__ == "__main__":
    app()
