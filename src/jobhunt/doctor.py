"""Environment health checks (`jobhunt doctor`)."""
from __future__ import annotations

import importlib.util
import sys

from .config import env, settings

REQUIRED = ["typer", "httpx", "pydantic", "yaml", "pandas", "jobspy", "rapidfuzz", "pypdf", "feedparser", "bs4",
            "dateutil", "tenacity", "dotenv"]
# Stealth / captcha-solving tooling has no place in this project (see docs/responsible-use.md).
FORBIDDEN = ["cloakbrowser", "httpcloak", "camoufox", "undetected_chromedriver", "nodriver", "capsolver",
             "patchright"]
OPTIONAL_KEYS = {"SERPAPI_KEY": "Google Jobs via SerpApi (optional)", "HIMALAYAS_API_KEY": "Himalayas (optional)",
                 "JOBHUNT_CONTACT": "contact shown in the HTTP User-Agent (recommended)"}


def run_checks() -> dict:
    s = settings()
    checks: list[dict] = []

    def add(name, ok, detail="", required=True):
        checks.append({"name": name, "ok": bool(ok), "required": required, "detail": detail})

    add("python>=3.11", sys.version_info[:2] >= (3, 11), sys.version.split()[0])
    for mod in REQUIRED:
        add(f"import:{mod}", importlib.util.find_spec(mod) is not None)
    for mod in FORBIDDEN:
        present = importlib.util.find_spec(mod) is not None
        add(f"absent:{mod}", not present, "stealth/captcha package installed — remove it" if present else "")
    try:
        s.ensure_dirs()
        add("dirs_writable", True)
    except OSError as e:
        add("dirs_writable", False, str(e))
    try:
        from .profile import validate_profile
        problems = validate_profile()
        add("profile", not problems, "valid" if not problems else "; ".join(problems)[:400])
    except Exception as e:  # noqa: BLE001
        add("profile", False, str(e)[:400])
    for k, what in OPTIONAL_KEYS.items():
        add(f"env:{k}", bool(env(k)), "set" if env(k) else f"not set — {what}", required=False)
    add("db", True, str(s.db_path), required=False)
    ok = all(c["ok"] for c in checks if c["required"])
    return {"ok": ok, "root": str(s.root), "profile_dir": str(s.profile_dir), "sample_unicode": "₹ → –",
            "checks": checks}
