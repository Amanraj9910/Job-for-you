"""jobhunt profile ... — create, fill, check and inspect your profile (preferences.yaml + resume.yaml)."""
from __future__ import annotations

import json
import re
import shutil
import webbrowser
import zipfile
from datetime import datetime
from importlib import resources
from pathlib import Path

import typer
import yaml

from ..config import settings
from ..util.out import emit, fail

app = typer.Typer(no_args_is_help=True, help="Your profile: preferences.yaml (what you want) + resume.yaml (facts).")
JSON = typer.Option(False, "--json")


def _load_raw(dir_: Path) -> dict:
    out = {}
    for key, name in (("preferences", "preferences.yaml"), ("resume", "resume.yaml")):
        p = dir_ / name
        if p.exists():
            out[key] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return out


@app.command()
def validate(json_: bool = JSON) -> None:
    """Check preferences.yaml and resume.yaml (schema, ids, skills, keywords). Exit 1 on problems."""
    from ..profile import profile_notes, validate_profile
    errors = validate_profile()
    notes = profile_notes() if not errors else []
    res = {"ok": not errors, "errors": errors, "notes": notes, "profile_dir": str(settings().profile_dir)}
    emit(res, json_, ("OK — profile is valid" if not errors else "PROBLEMS:\n" + "\n".join(errors))
         + "".join(f"\nnote: {n}" for n in notes))
    raise typer.Exit(0 if not errors else 1)


@app.command()
def show(json_: bool = JSON) -> None:
    """What the search and scoring will use (resolved defaults included)."""
    from ..geo import current
    from ..profile import preferences
    from ..resume import load_resume
    p, r, g = preferences(), load_resume(), current()
    res = {
        "home_country": g.home_cc, "tier1": list(g.tier1), "tier2": list(g.tier2),
        "remote_ok_scopes": sorted(g.ok_scopes), "abroad": p["location"]["abroad"],
        "target_titles": p["target_titles"], "title_keywords": p["title_keywords"],
        "queries": p["search"]["queries"], "search_locations": p["search"]["locations"] + p["search"]["remote_locations"],
        "experience_years": p["candidate"]["years_experience"], "max_required_years": p["experience"]["max_required_years"],
        "salary_floor": f"{p['salary']['min_per_year'] or 0:,.0f} {p['salary']['currency']}/year",
        "core_skills": p["core_skills"], "languages": p["languages"],
        "resume": {"skills": len(r.skills), "entries": len(r.entries), "bullets": len(r.bullets)},
    }
    emit(res, json_, yaml.safe_dump(res, sort_keys=False, allow_unicode=True, width=110))


@app.command()
def skills(json_: bool = JSON) -> None:
    """List your skill inventory (name, level, aliases)."""
    from ..resume import load_resume
    rows = [{"name": s.name, "level": s.level, "category": s.category, "aliases": s.aliases}
            for s in load_resume().skills]
    emit(rows, json_, "\n".join(f"{r['name']:<32} {r['level']:<9} {', '.join(r['aliases'])}" for r in rows))


@app.command()
def form(open_: bool = typer.Option(True, "--open/--no-open", help="Open it in your browser"),
         example: bool = typer.Option(False, "--example", help="Prefill from profile.example/ instead of yours"),
         json_: bool = JSON) -> None:
    """Write a fill-in form (HTML, works offline) prefilled with your profile; it downloads jobhunt-profile.json."""
    s = settings()
    data = _load_raw(s.example_profile_dir if example else s.profile_dir) or _load_raw(s.example_profile_dir)
    tpl = resources.files("jobhunt").joinpath("static/profile_form.html").read_text(encoding="utf-8")
    from ..geo import COUNTRY_NAMES, default_ok_scopes
    countries = {cc: {"name": n, "scopes": default_ok_scopes(cc)} for cc, n in COUNTRY_NAMES.items()}
    # countries first: the user's own text is substituted last, so it can never be re-interpreted
    page = (tpl.replace("__COUNTRIES__", json.dumps(countries, ensure_ascii=False).replace("</", "<\\/"))
               .replace("__PROFILE__", json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")))
    out = s.output_dir / "profile_form.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    if open_:
        webbrowser.open(out.resolve().as_uri())
    emit({"ok": True, "form": str(out)}, json_,
         f"Form written to {out}\nFill it in, click 'Download profile', then run:\n"
         f"  jobhunt profile import <path to>/jobhunt-profile.json")


@app.command("import")
def import_cmd(file: Path = typer.Argument(..., help="jobhunt-profile.json from the form, or a preferences/resume YAML"),
               json_: bool = JSON) -> None:
    """Install a profile produced by the form (or a single YAML file). Existing files are backed up first."""
    from ..profile import Preferences, reload, validate_profile
    from ..resume import Resume
    if not file.exists():
        fail(f"{file} not found", as_json=json_)
    text = file.read_text(encoding="utf-8")
    data = json.loads(text) if file.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        fail("expected a mapping", as_json=json_)
    parts = {}
    if "preferences" in data or "resume" in data:
        parts = {k: data[k] for k in ("preferences", "resume") if data.get(k)}
    elif data.get("kind") in ("preferences", "resume"):
        parts = {data["kind"]: data}
    else:
        fail("cannot tell what this file is: expected {preferences, resume} or a YAML with `kind:`", as_json=json_)
    errors = []
    for k, v in parts.items():
        try:
            (Preferences if k == "preferences" else Resume).model_validate(v)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{k}: {e}")
    if errors:
        fail("not imported, the file has problems:\n" + "\n".join(errors), as_json=json_)
    d = settings().profile_dir
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    written = []
    for k, v in parts.items():
        p = d / f"{k}.yaml"
        if p.exists():
            shutil.copy2(p, d / f"{k}.{stamp}.bak.yaml")
        p.write_text(f"# Imported {stamp} from {file.name}. Edit freely; `jobhunt profile validate` checks it.\n"
                     + yaml.safe_dump(v, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
        written.append(str(p))
    reload()
    problems = validate_profile()
    emit({"ok": not problems, "written": written, "problems": problems}, json_,
         "\n".join([f"wrote {w}" for w in written] + (["PROBLEMS:", *problems] if problems else ["profile is valid"])))


@app.command("extract-text")
def extract_text(file: Path = typer.Argument(..., help="Your resume: .pdf, .docx, .md, .txt or .tex"),
                 json_: bool = JSON) -> None:
    """Print the plain text of a resume file, so any coding agent can read it and draft resume.yaml."""
    if not file.exists():
        fail(f"{file} not found", as_json=json_)
    suf = file.suffix.lower()
    if suf == ".pdf":
        from pypdf import PdfReader
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(file)).pages)
    elif suf == ".docx":
        with zipfile.ZipFile(file) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        text = re.sub(r"<[^>]+>", "", xml)
    else:
        text = file.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    emit({"file": str(file), "chars": len(text), "text": text}, json_, text)
