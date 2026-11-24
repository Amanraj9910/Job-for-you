"""CLI flow on an isolated DB: match run -> packet -> ingest -> vet -> report build -> report sync."""
import json

import pytest
from typer.testing import CliRunner

from jobhunt import db
from jobhunt.cli import app
from jobhunt.models import Job
from jobhunt.sources.base import finalize

JD = """About Acme AI
Acme builds LLM products for enterprises.

Requirements:
- 1-3 years of experience building LLM applications in Python
- Hands-on with RAG and vector databases
- REST APIs with FastAPI
- Experience with AWS

Nice to have:
- Kubernetes
- Computer vision

Responsibilities
- Build and deploy AI features for customers
"""

runner = CliRunner()


def run(*args):
    r = runner.invoke(app, [*args, "--json"])
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


@pytest.fixture
def seeded(tmp_home):
    conn = db.connect()
    good = finalize(Job(source="lever", source_job_id="g1", title="AI Engineer", company="Acme AI",
                        location="Bengaluru, India", apply_url="https://jobs.lever.co/acme/g1/apply", jd_text=JD),
                    jd_is_html=False)
    bad = finalize(Job(source="lever", source_job_id="b1", title="Principal AI Engineer", company="Acme AI",
                       location="Bengaluru", apply_url="https://jobs.lever.co/acme/b1/apply", jd_text="10+ years"),
                   jd_is_html=False)
    return conn, db.upsert_job(conn, good), db.upsert_job(conn, bad)


def test_full_flow(seeded, tmp_path, tmp_home):
    conn, gid, bid = seeded
    res = run("match", "run")
    assert res["gate_skipped"] == 1
    assert [x["job_id"] for x in res["shortlist"]] == [gid]
    assert db.get_job(conn, bid)["status"] == "skipped"

    packet = run("match", "packet", "--job", gid)
    reqs = packet["requirements"]
    assert len(reqs) >= 6
    bullet_ids = {b["id"] for b in packet["candidate"]["bullets"]}
    assert "EXP_ACME.B1" in bullet_ids

    judgment = {
        "job_id": gid, "years": {"required": 1, "candidate": 2}, "seniority_match": "exact", "domain_fit": 0.9,
        "confidence": "high", "summary": "strong fit",
        "requirements": [{"req_id": r["req_id"], "kind": r["kind"],
                          "status": "met" if r["kind"] != "nice" else "partial",
                          "evidence": ["EXP_ACME.B1"]} for r in reqs],
    }
    jf = tmp_path / "j.json"
    jf.write_text(json.dumps(judgment), encoding="utf-8")
    scored = run("match", "ingest", "--job", gid, "--file", str(jf))
    assert scored["fit_score"] >= 80 and scored["band"] == "apply"
    assert db.get_job(conn, gid)["status"] == "shortlisted"

    pending = run("vet", "pending")
    assert [c["company"] for c in pending] == ["Acme AI"]
    packet = run("vet", "packet", "--company", "Acme AI")
    assert any("Glassdoor" in q for q in packet["queries"]) and packet["rules"]
    verdict = {"company": "Acme AI", "verdict": "ok", "ratings": [{"site": "glassdoor", "rating": 4.2, "reviews": 120}],
               "reasons": [{"text": "solid reviews", "url": "https://example.com/acme", "kind": "legit"}],
               "posting_on_official_site": True}
    vf = tmp_path / "v.json"
    vf.write_text(json.dumps(verdict), encoding="utf-8")
    assert run("vet", "ingest", "--file", str(vf))["verdict"] == "ok"

    out = tmp_path / "jobs.html"
    built = run("report", "build", "--out", str(out))
    assert built["rows"] == 2 and built["vetted_ok"] == 2 and built["direct"] == 2   # same company
    page = out.read_text(encoding="utf-8")
    assert "__DATA__" not in page and "__TITLE__" not in page and "Sam&#x27;s job matches" in page
    rows = json.loads(page.split("const JOBS = ", 1)[1].split(";\nconst KEY", 1)[0])
    good_row = next(r for r in rows if r["id"] == gid)
    assert good_row["vf"] == "ok" and good_row["fit"] == 1 and good_row["region"] == "Home"
    assert next(r for r in rows if r["id"] == bid)["skip"] == 1

    ticks = tmp_path / "applied_ticks.json"
    ticks.write_text(json.dumps({"jobs": {gid: {"applied": True, "on": "2026-11-24", "note": "applied via site"}}}),
                     encoding="utf-8")
    synced = run("report", "sync", str(ticks))
    assert synced["synced"]["applied"] == 1 and synced["synced"]["notes"] == 1
    assert db.get_job(conn, gid)["status"] == "applied"


def test_vet_bond_jd_rejects(tmp_home, tmp_path):
    conn = db.connect()
    j = finalize(Job(source="lever", source_job_id="x", title="AI Engineer", company="BondCo", location="Pune",
                     apply_url="https://jobs.lever.co/bondco/x", jd_text="Selected candidates sign a 2 year service "
                     "bond. Registration fee of Rs 5000 applies."), jd_is_html=False)
    db.upsert_job(conn, j)
    verdict = {"company": "BondCo", "verdict": "ok",
               "reasons": [{"text": "looks fine", "url": "https://example.com", "kind": "legit"}]}
    vf = tmp_path / "v.json"
    vf.write_text(json.dumps(verdict), encoding="utf-8")
    res = run("vet", "ingest", "--file", str(vf))
    assert res["verdict"] == "reject"
    assert {f["flag"] for f in res["jd_flags"]} >= {"service_bond", "candidate_fee"}


def test_init_validate_and_profile_round_trip(tmp_path, monkeypatch):
    """init copies the example profile; form JSON imports back; validate passes."""
    from jobhunt import config, geo
    prof = tmp_path / "profile"
    monkeypatch.setenv("JOBHUNT_PROFILE", str(prof))
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("JOBHUNT_OUTPUT", str(tmp_path / "out"))
    config.settings.cache_clear()
    geo.configure(None)            # this test checks the profile-driven geography
    try:
        res = run("init")
        assert len(res["copied"]) == 2 and (prof / "preferences.yaml").exists()
        assert run("profile", "validate")["ok"] is True
        form = run("profile", "form", "--no-open")
        page = (tmp_path / "out" / "profile_form.html").read_text(encoding="utf-8")
        assert form["ok"] and "__PROFILE__" not in page and "__COUNTRIES__" not in page

        import yaml
        bundle = {"preferences": yaml.safe_load((prof / "preferences.yaml").read_text(encoding="utf-8")),
                  "resume": yaml.safe_load((prof / "resume.yaml").read_text(encoding="utf-8"))}
        bundle["preferences"]["location"]["home_country"] = "DE"
        bundle["preferences"]["location"]["tier1"] = ["Berlin"]
        bundle["preferences"]["location"]["remote_ok_scopes"] = None
        f = tmp_path / "jobhunt-profile.json"
        f.write_text(json.dumps(bundle), encoding="utf-8")
        imp = run("profile", "import", str(f))
        assert imp["ok"] and len(imp["written"]) == 2
        assert list(prof.glob("preferences.*.bak.yaml"))
        shown = run("profile", "show")
        assert shown["home_country"] == "DE" and "EU" in shown["remote_ok_scopes"]
    finally:
        config.settings.cache_clear()


def test_import_rejects_invalid(tmp_path, monkeypatch):
    from jobhunt import config
    monkeypatch.setenv("JOBHUNT_PROFILE", str(tmp_path / "p"))
    config.settings.cache_clear()
    try:
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({"preferences": {"kind": "preferences", "target_titles": []}}), encoding="utf-8")
        r = runner.invoke(app, ["profile", "import", str(f), "--json"])
        assert r.exit_code != 0 and "problems" in r.output
        assert not (tmp_path / "p" / "preferences.yaml").exists()
    finally:
        config.settings.cache_clear()
