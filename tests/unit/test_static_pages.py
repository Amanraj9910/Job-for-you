"""The two HTML pages (jobs report, profile form) render without leftover placeholders and valid JavaScript."""
import json
import re
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from jobhunt.cli import app

NODE = shutil.which("node")


def _scripts(page: str) -> str:
    return "\n;\n".join(re.findall(r"<script>(.*?)</script>", page, flags=re.S))


def _node_check(js: str, tmp_path) -> None:
    if not NODE:
        pytest.skip("node not installed")
    f = tmp_path / "page.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_report_page(tmp_home, tmp_path):
    r = CliRunner().invoke(app, ["report", "build", "--out", str(tmp_path / "jobs.html"), "--json"])
    assert r.exit_code == 0, r.output
    page = (tmp_path / "jobs.html").read_text(encoding="utf-8")
    assert not re.search(r"__[A-Z]+__", page)
    _node_check(_scripts(page), tmp_path)


def test_profile_form_page(tmp_home, tmp_path):
    r = CliRunner().invoke(app, ["profile", "form", "--no-open", "--example", "--json"])
    assert r.exit_code == 0, r.output
    page = open(json.loads(r.output)["form"], encoding="utf-8").read()
    assert "__PROFILE__" not in page and "__COUNTRIES__" not in page and "Sam Example" in page
    _node_check(_scripts(page), tmp_path)
