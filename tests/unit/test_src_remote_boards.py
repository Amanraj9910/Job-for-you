"""Remote-board sources: himalayas, remotive, remoteok, wwr, hn, yc_jobs (fixtures recorded live 2026-10-27)."""
import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from jobhunt.models import Channel, RemoteType
from jobhunt.sources import himalayas, hn, remoteok, remotive, wwr, yc_jobs
from jobhunt.sources.base import REGISTRY, SearchContext
from jobhunt.util import http as H

FX = Path(__file__).resolve().parents[1] / "fixtures"


def fx_json(rel):
    return json.loads((FX / rel).read_text(encoding="utf-8"))


def fx_text(rel):
    return (FX / rel).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def fast_http(monkeypatch, tmp_path):
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr(H.Http.request.retry, "sleep", lambda s: None)
    H._last_hit.clear()
    monkeypatch.setattr(remotive, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(hn, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.delenv("HIMALAYAS_API_KEY", raising=False)


def ctx(**kw):
    return SearchContext(client=H.Http(), **kw)


def test_all_registered():
    for name in ("himalayas", "remotive", "remoteok", "wwr", "hn", "yc_jobs"):
        assert name in REGISTRY


# ------------------------------------------------------------------ himalayas
HIM = dict(host="himalayas.app", path="/jobs/api/search")


@respx.mock
def test_himalayas_happy_path_and_geo_filter():
    route = respx.get(**HIM).mock(return_value=httpx.Response(200, json=fx_json("himalayas/search_python.json")))
    res = himalayas.search(ctx(queries=["python"]))
    assert not res.errors and res.calls == 1
    assert route.calls[0].request.url.params["country"] == "India"
    by = {j.company: j for j in res.jobs}
    # Real Python is restricted to CA/UK/US -> dropped; worldwide, India and APAC lists kept
    assert set(by) == {"Alien Intellect", "ITHR Technologies Consulting LLC", "DevGrid", "Kodland"}
    ithr = by["ITHR Technologies Consulting LLC"]
    assert ithr.source == "himalayas" and ithr.title == "Senior Python Developer"
    assert ithr.source_job_id.startswith("ithr-technologies-consulting-llc/")
    assert ithr.apply_url.startswith("https://himalayas.app/companies/ithr-technologies-consulting-llc/jobs/")
    assert ithr.source_url.startswith("https://himalayas.app/") and "utm_source" not in ithr.source_url
    assert ithr.remote_type == RemoteType.REMOTE and ithr.remote_scope == ["IN"] and ithr.location_tier == 3
    assert ithr.posted_at is not None and ithr.jd_text and "<" not in ithr.jd_text[:50]
    alien = by["Alien Intellect"]
    assert alien.remote_scope == ["WORLDWIDE"]
    assert (alien.salary_min, alien.salary_max, alien.salary_currency, alien.salary_period) == (
        2000.0, 3000.0, "USD", "month")
    assert set(by["Kodland"].remote_scope) >= {"IN", "APAC"}
    assert all(j.channel == Channel.MANUAL_OTHER for j in res.jobs)   # applicationLink is a himalayas.app URL
    assert all(set(j.raw) == {"guid"} for j in res.jobs)


@respx.mock
def test_himalayas_title_filter_and_api_key(monkeypatch):
    monkeypatch.setenv("HIMALAYAS_API_KEY", "k123")
    route = respx.get(**HIM).mock(return_value=httpx.Response(200, json=fx_json("himalayas/search_python.json")))
    res = himalayas.search(ctx(queries=["python"], title_include=["machine learning"]))
    assert [j.company for j in res.jobs] == ["DevGrid"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer k123"


@respx.mock
def test_himalayas_empty_and_500_never_raise():
    respx.get(**HIM).mock(return_value=httpx.Response(200, json={"jobs": [], "totalCount": 0}))
    res = himalayas.search(ctx(queries=["nothing"]))
    assert res.jobs == [] and res.errors == []
    respx.get(**HIM).mock(return_value=httpx.Response(500))
    res = himalayas.search(ctx(queries=["python"]))
    assert res.jobs == [] and res.errors and "HTTPStatusError" in res.errors[0] and res.calls == 1


@respx.mock
def test_himalayas_paging_respects_budget_and_limit():
    page = fx_json("himalayas/search_python.json")
    jobs = page["jobs"]
    full = dict(page, totalCount=100, jobs=[dict(jobs[i % len(jobs)], guid=f"https://himalayas.app/companies/x/jobs/j{i}")
                                            for i in range(20)])
    route = respx.get(**HIM).mock(return_value=httpx.Response(200, json=full))
    res = himalayas.search(ctx(queries=["python"], budgets={"himalayas": 2}, limit=500))
    assert route.call_count == 2 and res.calls == 2
    assert route.calls[1].request.url.params["page"] == "2"
    res = himalayas.search(ctx(queries=["python"], limit=3))
    assert len(res.jobs) == 3


def test_himalayas_scope_tokens():
    assert himalayas.scope_tokens([]) == ["WORLDWIDE"]
    assert himalayas.scope_tokens(["India"]) == ["IN"]
    assert himalayas.scope_tokens(["Canada", "United Kingdom", "United States"]) == ["CA", "UK", "US"]
    assert himalayas.scope_tokens(["Germany", "France"]) == ["EU"]
    assert himalayas.scope_tokens(["Serbia"]) == ["SERBIA"]


# ------------------------------------------------------------------ remotive
REM = dict(host="remotive.com", path="/api/remote-jobs")


@respx.mock
def test_remotive_happy_path_and_cap_file(tmp_path):
    route = respx.get(**REM).mock(return_value=httpx.Response(200, json=fx_json("remotive/search_ml.json")))
    res = remotive.search(ctx(queries=["machine learning"]))
    assert not res.errors and res.calls == 1
    assert route.calls[0].request.url.params["search"] == "machine learning"
    titles = {j.title for j in res.jobs}
    assert "Tech Lead Full-Stack Rails Engineer" not in titles          # USA/Canada only
    ai = next(j for j in res.jobs if j.title == "AI Trainer - Video Annotation")
    assert ai.source == "remotive" and ai.source_job_id == "2091161" and ai.company == "iMerit Technology"
    assert ai.source_url == ai.apply_url and ai.source_url.startswith("https://remotive.com/remote-jobs/")
    assert ai.remote_scope == ["WORLDWIDE"] and ai.location_tier == 4
    assert (ai.salary_min, ai.salary_max) == (70000.0, 80000.0)
    assert ai.channel == Channel.MANUAL_OTHER and ai.employment_type == "freelance"
    calls = json.loads((tmp_path / "cache" / "remotive_calls.json").read_text())
    assert len(calls) == 1


@respx.mock
def test_remotive_daily_cap_blocks_calls(tmp_path):
    d = tmp_path / "cache"
    d.mkdir(parents=True)
    now = time.time()
    (d / "remotive_calls.json").write_text(json.dumps([now - 60 * i for i in range(4)] + [now - 90000]))
    route = respx.get(**REM).mock(return_value=httpx.Response(200, json=fx_json("remotive/search_ml.json")))
    res = remotive.search(ctx(queries=["python"]))
    assert route.call_count == 0 and res.calls == 0 and res.jobs == []
    assert "daily cap" in res.errors[0]
    assert remotive.DailyCap("remotive", 4).used() == 4                 # the >24h-old entry does not count


@respx.mock
def test_remotive_title_filter_and_500():
    respx.get(**REM).mock(return_value=httpx.Response(200, json=fx_json("remotive/search_ml.json")))
    res = remotive.search(ctx(queries=["ai"], title_include=["engineer"]))
    assert [j.title for j in res.jobs] == ["Software Engineer, AI Coding Tasks"]
    respx.get(**REM).mock(return_value=httpx.Response(500))
    res = remotive.search(ctx(queries=["ai"]))
    assert res.jobs == [] and res.errors and res.calls == 1


@respx.mock
def test_remotive_without_queries_uses_category():
    route = respx.get(**REM).mock(return_value=httpx.Response(200, json={"jobs": []}))
    res = remotive.search(ctx())
    assert res.jobs == [] and not res.errors
    assert route.calls[0].request.url.params["category"] == "software-development"


# ------------------------------------------------------------------ remoteok
ROK = dict(host="remoteok.com", path="/api")


@respx.mock
def test_remoteok_parsing_filters_and_mojibake():
    respx.get(**ROK).mock(return_value=httpx.Response(200, json=fx_json("remoteok/api.json")))
    res = remoteok.search(ctx(title_include=["engineer"]))
    assert not res.errors and res.calls == 1
    by = {j.company: j for j in res.jobs}
    assert set(by) == {"Klardaten", "Lemon.io", "DuckDuckGo"}
    k = by["Klardaten"]
    assert k.source == "remoteok" and k.source_job_id == "1137571" and k.title == "Senior Software Engineer"
    assert k.source_url.lower().startswith("https://remoteok.com/remote-jobs/")
    assert "We’re" in k.jd_text and "â" not in k.jd_text
    assert (k.salary_min, k.salary_max, k.salary_currency) == (80000.0, 100000.0, "USD")
    assert k.remote_type == RemoteType.REMOTE and k.channel == Channel.MANUAL_OTHER


@respx.mock
def test_remoteok_query_match_on_tags_salary_zero_and_geo():
    respx.get(**ROK).mock(return_value=httpx.Response(200, json=fx_json("remoteok/api.json")))
    res = remoteok.search(ctx(queries=["python"]))
    assert {j.title for j in res.jobs} == {"Senior DevOps Engineer", "Risk Analyst"}   # via tags
    risk = next(j for j in res.jobs if j.title == "Risk Analyst")
    assert risk.salary_min is None and risk.salary_max is None                      # 0 == not listed
    res = remoteok.search(ctx(title_include=["support"]))
    assert res.jobs == []                     # "Seattle, Washington" remote job is US-bound -> dropped


@respx.mock
def test_remoteok_cap_and_500(tmp_path):
    route = respx.get(**ROK).mock(return_value=httpx.Response(500))
    res = remoteok.search(ctx(budgets={"remoteok_per_day": 1}))
    assert res.jobs == [] and res.errors and res.calls == 1
    res = remoteok.search(ctx(budgets={"remoteok_per_day": 1}))
    assert "daily cap" in res.errors[0] and res.calls == 0
    assert route.call_count == 3                                         # 3 retries of the first call only


def test_fix_mojibake():
    assert remoteok.fix_mojibake("Weâ\u0080\u0099re") == "We’re"
    assert remoteok.fix_mojibake("Café ₹") == "Café ₹"


# ------------------------------------------------------------------ wwr
@respx.mock
def test_wwr_rss_parsing_and_region_scope():
    route = respx.get(host="weworkremotely.com").mock(
        return_value=httpx.Response(200, content=(FX / "wwr/remote-programming-jobs.rss").read_bytes()))
    res = wwr.search(ctx(budgets={"wwr_feeds": ["remote-programming-jobs"]}))
    assert not res.errors and res.calls == 1 and route.call_count == 1
    assert str(route.calls[0].request.url) == \
        "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    by = {j.company: j for j in res.jobs}
    assert set(by) == {"Webflow", "Wrike", "A.Team"}           # Americas-only and Helsinki dropped
    w = by["Webflow"]
    assert w.title == "Senior Infrastructure Engineer" and w.source == "wwr"
    assert w.source_job_id == "webflow-senior-infrastructure-engineer"
    assert w.remote_scope == ["WORLDWIDE"] and w.location_tier == 4  # "Anywhere in the World" is not India
    assert w.posted_at.year == 2026 and w.channel == Channel.MANUAL_OTHER
    assert "Webflow is the agentic web" in w.jd_text


@respx.mock
def test_wwr_title_filter_404_and_500():
    respx.get(host="weworkremotely.com", path="/categories/remote-programming-jobs.rss").mock(
        return_value=httpx.Response(200, content=(FX / "wwr/remote-programming-jobs.rss").read_bytes()))
    respx.get(host="weworkremotely.com", path="/categories/gone.rss").mock(return_value=httpx.Response(404))
    respx.get(host="weworkremotely.com", path="/categories/broken.rss").mock(return_value=httpx.Response(500))
    res = wwr.search(ctx(title_include=["ai"], budgets={"wwr_feeds": ["gone", "broken", "remote-programming-jobs"]}))
    assert [j.company for j in res.jobs] == ["A.Team"]
    assert len(res.errors) == 1 and "broken" in res.errors[0] and res.calls == 3


# ------------------------------------------------------------------ hn
ALG = "hn.algolia.com"


def _hn_routes():
    respx.get(host=ALG, path="/api/v1/search_by_date").mock(
        return_value=httpx.Response(200, json=fx_json("hn/stories.json")))
    return respx.get(host=ALG, path="/api/v1/search").mock(
        return_value=httpx.Response(200, json=fx_json("hn/comments_49922569.json")))


@respx.mock
def test_hn_fetch_thread_filters_and_writes_file(tmp_path):
    croute = _hn_routes()
    t = hn.fetch_thread(client=H.Http())
    assert t.story_id == 49922569 and "October 2026" in t.title
    assert croute.calls[0].request.url.params["tags"] == "comment,story_49922569"
    assert t.total_top_level == 5                                  # nested reply excluded
    ids = {c["comment_id"] for c in t.comments}
    assert ids == {"50095249", "50090302", "50068986"}             # REMOTE + AI; onsite/Indiana dropped
    assert t.path == tmp_path / "cache" / "hn_49922569.json"
    saved = json.loads(t.path.read_text(encoding="utf-8"))
    assert saved[0]["url"].startswith("https://news.ycombinator.com/item?id=")
    assert "<p>" not in saved[0]["text"] and "&#x2F;" not in saved[0]["text"]


@respx.mock
def test_hn_month_selection():
    _hn_routes()
    assert hn.find_story("2026-09", H.Http())["objectID"] == "49522897"
    assert hn.find_story("October 2026", H.Http())["objectID"] == "49922569"
    assert hn.find_story("1999-01", H.Http()) is None


def test_hn_ingest_file(tmp_path):
    rows = [
        {"company": "Acme AI", "title": "ML Engineer", "location": "Bengaluru", "remote": False,
         "apply_url": "https://jobs.lever.co/acme/123", "salary": "₹30-40 LPA", "description": "3+ years of ML",
         "comment_id": 50095249},
        {"company": "Beta", "title": "LLM Engineer", "location": None, "remote": "yes",
         "apply_url": "jobs@beta.dev", "salary": None, "description": "Build agents", "comment_id": "50090302"},
        {"company": "", "title": "Broken", "comment_id": "1"},
        {"company": "NoId", "title": "Eng", "comment_id": "abc"},
    ]
    p = tmp_path / "hn_extract.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    errs: list[str] = []
    jobs = hn.ingest_file(p, errs)
    assert len(jobs) == 2 and len(errs) == 2
    a, b = jobs
    assert a.source == "hn" and a.source_job_id.startswith("50095249-") and len(a.source_job_id) == 17
    assert a.channel == Channel.AUTO and a.ats_type == "lever" and a.location_tier == 2
    assert a.salary_currency == "INR" and a.exp_min == 3
    assert a.source_url == "https://news.ycombinator.com/item?id=50095249"
    assert b.apply_url == "mailto:jobs@beta.dev" and b.channel == Channel.MANUAL_EMAIL
    assert b.remote_type == RemoteType.REMOTE


def test_hn_search_is_informational():
    res = hn.search(ctx())
    assert res.jobs == [] and res.calls == 0 and "agent extraction" in res.errors[0]


# ------------------------------------------------------------------ yc_jobs
YC = "www.ycombinator.com"


def _yc_routes(listing=None):
    lst = respx.get(host=YC, path__startswith="/jobs/").mock(
        return_value=listing or httpx.Response(200, text=fx_text("yc_jobs/location_india.html")))
    det = respx.get(host=YC, path__startswith="/companies/").mock(
        return_value=httpx.Response(200, text=fx_text("yc_jobs/job_signoz.html")))
    return lst, det


@respx.mock
def test_yc_listing_detail_and_geo():
    lst, det = _yc_routes()
    res = yc_jobs.search(ctx())
    assert not res.errors
    assert lst.call_count == 3                                     # 3 listing pages, postings deduped by id
    by = {j.company: j for j in res.jobs}
    assert set(by) == {"Cyble", "LiteLLM", "GoSats", "Spenmo"}     # FirstWork "Remote (US)" dropped
    assert det.call_count == 4 and res.calls == 7
    g = by["GoSats"]
    assert g.source == "yc_jobs" and g.location_tier == 2 and g.salary_currency == "INR"
    assert g.source_url.startswith("https://www.ycombinator.com/companies/gosats/jobs/")
    assert g.channel == Channel.MANUAL_OTHER
    assert g.raw["company_website"] == "https://signoz.io" and g.raw["company_slug"] == "gosats"
    assert "About the role" in g.jd_text
    assert by["LiteLLM"].title == "Forward Deployed Engineer - India"   # India in title rescues "Remote (US)"
    assert by["Cyble"].exp_min == 6 and by["Cyble"].posted_at is not None


@respx.mock
def test_yc_title_filter_before_detail():
    _, det = _yc_routes()
    res = yc_jobs.search(ctx(title_include=["engineer"], budgets={"yc_jobs_paths": ["/jobs/location/india"]}))
    assert {j.company for j in res.jobs} == {"Cyble", "LiteLLM", "Spenmo"}
    assert det.call_count == 3                                     # no detail fetch for filtered-out titles


@respx.mock
def test_yc_500_and_layout_change_never_raise():
    _yc_routes(httpx.Response(500))
    res = yc_jobs.search(ctx(budgets={"yc_jobs_paths": ["/jobs/location/india"]}))
    assert res.jobs == [] and res.errors and res.calls == 1
    respx.get(host=YC, path__startswith="/jobs/").mock(return_value=httpx.Response(200, text="<html></html>"))
    res = yc_jobs.search(ctx(budgets={"yc_jobs_paths": ["/jobs/location/india"]}))
    assert res.jobs == [] and "no jobPostings" in res.errors[0]


# ------------------------------------------------------------------ shared helpers
def test_geo_ok_and_safe_location():
    from jobhunt.models import Job
    from jobhunt.sources.base import finalize
    def mk(loc):
        return finalize(Job(source="t", source_job_id="1", title="x", company="y",
                            location=himalayas.safe_location(loc)), is_remote=True)
    assert himalayas.geo_ok(mk("Remote"))
    assert himalayas.geo_ok(mk("Remote - India"))
    assert himalayas.geo_ok(mk("Anywhere in the World"))
    assert mk("Anywhere in the World").location_tier == 4
    assert not himalayas.geo_ok(mk("Remote (US only)"))
    assert not himalayas.geo_ok(mk("Seattle, Washington"))
    assert himalayas.geo_ok(mk("APAC"))
