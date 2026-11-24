"""search.run with fake sources; dedupe into canonical jobs; watchlist state."""
from jobhunt import db
from jobhunt.dedupe import assign_canonical, group_of, same_role
from jobhunt.models import Job
from jobhunt.search import add_to_watchlist, run
from jobhunt.sources import base as sbase
from jobhunt.sources.base import SearchContext, SourceResult, finalize


def mk(source, sid, title="AI Engineer", company="Acme AI Pvt Ltd", loc="Bengaluru", url=None, jd="x" * 50):
    return finalize(Job(source=source, source_job_id=sid, title=title, company=company, location=loc,
                        apply_url=url, jd_text=jd), jd_is_html=False)


def test_same_role_rules():
    a = {"company_key": "acme ai", "title": "Senior AI Engineer", "location": "Bengaluru", "first_seen": "2026-10-01T00:00:00+00:00"}
    b = {"company_key": "acme ai", "title": "AI Engineer", "location": "Bangalore, India", "first_seen": "2026-10-05T00:00:00+00:00"}
    c = {"company_key": "acme ai", "title": "AI Engineer", "location": "Pune", "first_seen": "2026-10-05T00:00:00+00:00"}
    assert same_role(a, b)          # senior stripped by title_key, same city
    assert not same_role(b, c)      # different city


def test_three_sources_one_canonical(tmp_home):
    conn = db.connect()
    gh = mk("greenhouse", "1", url="https://boards.greenhouse.io/acme/jobs/1", jd="long jd " * 100)
    li = mk("jobspy_linkedin", "2", company="Acme AI", url="https://www.linkedin.com/jobs/view/2")
    ind = mk("jobspy_indeed", "3", company="ACME AI Private Limited", url="https://in.indeed.com/viewjob?jk=3")
    ids = [db.upsert_job(conn, j) for j in (li, ind, gh)]
    stats = assign_canonical(conn, ids)
    assert stats["duplicates"] == 2
    canon = {db.get_job(conn, i)["canonical_job_id"] for i in ids}
    assert canon == {gh.job_id}
    assert len(group_of(conn, li.job_id)) == 3


def test_search_run_with_fake_sources(tmp_home, monkeypatch):
    conn = db.connect()
    add_to_watchlist(conn, "greenhouse", "acme", "Acme", state="active")
    add_to_watchlist(conn, "greenhouse", "deadco", "Dead", state="active")

    def fake_gh(ctx):
        assert "acme" in ctx.boards["greenhouse"]
        return SourceResult("greenhouse", [mk("greenhouse", "9", url="https://boards.greenhouse.io/acme/jobs/9")],
                            errors=["deadco: 404"], calls=2)

    def boom(ctx):
        raise RuntimeError("kaput")

    monkeypatch.setitem(sbase.REGISTRY, "greenhouse", fake_gh)
    monkeypatch.setitem(sbase.REGISTRY, "explodes", boom)
    monkeypatch.setattr("jobhunt.search.load_sources", lambda: sbase.REGISTRY)
    ctx = SearchContext(boards={"greenhouse": ["acme", "deadco"]})
    res = run(conn, ["greenhouse", "explodes", "missing"], ctx)
    assert res["new_jobs"] == 1
    assert res["sources"]["explodes"]["ok"] is False
    assert res["sources"]["missing"]["error"] == "not registered"
    wl = {r["ats_slug"]: dict(r) for r in conn.execute("SELECT * FROM watchlist")}
    assert wl["acme"]["state"] == "active" and wl["acme"]["n_jobs"] == 1
    assert wl["deadco"]["fail_count"] == 1
    # second run: job is not new any more
    assert run(conn, ["greenhouse"], ctx)["new_jobs"] == 0
    health = conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0]
    assert health == 4
