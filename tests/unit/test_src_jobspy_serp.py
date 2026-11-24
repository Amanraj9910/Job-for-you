"""Unit tests for jobspy_src (jobspy_indeed/glassdoor/naukri/linkedin) and serpapi. No network."""
import json
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from jobhunt.models import Channel, RemoteType
from jobhunt.sources import jobspy_src as J
from jobhunt.sources import serpapi as S
from jobhunt.sources.base import REGISTRY, SearchContext
from jobhunt.util import http as H

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _df(site: str) -> pd.DataFrame:
    return pd.read_csv(FIX / "jobspy" / f"{site}.csv")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(J, "_sleep", lambda s: slept.append(s))
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr(H.Http.request.retry, "sleep", lambda s: None)
    H._last_hit.clear()
    return slept


class FakeScrape:
    """Stand-in for jobspy.scrape_jobs: returns queued DataFrames / errors and records kwargs."""

    def __init__(self, frames=None, errors=None, exc=None):
        self.frames, self.errors, self.exc, self.calls = list(frames or []), list(errors or []), exc, []

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        df = self.frames.pop(0) if self.frames else pd.DataFrame(columns=_df("indeed").columns)
        err = self.errors.pop(0) if self.errors else None
        df = df.copy()
        df.attrs = {"errors": {kw["site_name"][0]: err} if err else {}, "warnings": {}}
        return df


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        import jobspy
        f = FakeScrape(**kw)
        monkeypatch.setattr(jobspy, "scrape_jobs", f)
        return f
    return install


def ctx(**kw) -> SearchContext:
    base = {"queries": ["python developer"], "locations": ["Bengaluru"], "hours_old": 72, "limit": 50}
    base.update(kw)
    return SearchContext(**base)


# ------------------------------------------------------------------ jobspy mapping
def test_registered():
    for n in ("jobspy_indeed", "jobspy_glassdoor", "jobspy_naukri", "jobspy_linkedin", "serpapi"):
        assert n in REGISTRY


def test_indeed_rows_map_to_jobs():
    jobs = J.df_to_jobs(_df("indeed"), "indeed")
    assert len(jobs) == 5
    j = jobs[0]
    assert j.source == "jobspy_indeed" and j.source_job_id == "in-2f8042be03db8390"
    assert j.title == "Python Full Stack Developer LEAD" and j.company == "gusri solutions"
    assert j.source_url == j.apply_url == "https://in.indeed.com/viewjob?jk=2f8042be03db8390"
    assert (j.salary_min, j.salary_max, j.salary_currency, j.salary_period) == (1800000.0, 2000000.0, "INR", "year")
    assert j.posted_at.year == 2026 and j.posted_at.tzinfo is not None
    assert j.country == "IN" and j.location_tier == 3
    assert j.channel == Channel.MANUAL_OTHER            # board URL, no direct link
    assert "<" not in j.jd_text and "Python" in j.jd_text
    monthly = next(x for x in jobs if x.source_job_id == "in-974a84b771d19841")
    assert monthly.salary_period == "month"
    assert set(j.raw) <= {"jobspy_id", "site", "company_url_direct", "emails"}


def test_direct_url_becomes_apply_url_and_classifies_ats():
    df = _df("indeed")
    df["job_url_direct"] = df["job_url_direct"].astype(object)
    df.loc[0, "job_url_direct"] = "https://job-boards.greenhouse.io/acme/jobs/123"
    df.loc[1, "job_url_direct"] = "https://careers.example-employer.com/jobs/42"
    jobs = J.df_to_jobs(df, "indeed")
    assert jobs[0].apply_url == "https://job-boards.greenhouse.io/acme/jobs/123"
    assert jobs[0].source_url.startswith("https://in.indeed.com/")
    assert jobs[0].channel == Channel.AUTO and jobs[0].ats_type == "greenhouse" and jobs[0].ats_slug == "acme"
    assert jobs[1].channel == Channel.AUTO_PROBE


def test_glassdoor_rows():
    jobs = J.df_to_jobs(_df("glassdoor"), "glassdoor")
    assert len(jobs) == 5
    assert {j.source for j in jobs} == {"jobspy_glassdoor"}
    bl = [j for j in jobs if j.location == "Bengaluru, India"]
    assert bl and all(j.location_tier == 2 for j in bl)
    assert all(j.channel == Channel.MANUAL_OTHER for j in jobs)
    assert jobs[0].salary_min is None and jobs[1].salary_max == 819000.0


@pytest.mark.parametrize("site", ["naukri", "linkedin"])
def test_linkedin_naukri_are_manual(site):
    jobs = J.df_to_jobs(_df(site), site)
    assert len(jobs) == 5
    for j in jobs:
        assert j.source == f"jobspy_{site}"
        assert j.source_url == j.apply_url
        assert j.channel == Channel.MANUAL_LINKEDIN_NAUKRI
        assert j.location_tier == 2


def test_naukri_work_from_office_and_experience():
    jobs = J.df_to_jobs(_df("naukri"), "naukri")
    assert all(j.remote_type == RemoteType.ONSITE for j in jobs)
    assert any(j.exp_min is not None for j in jobs)


def test_salary_weekly_and_remote_flag():
    df = _df("linkedin").head(1).copy()
    df["min_amount"], df["max_amount"], df["currency"], df["interval"] = 1000.0, 2000.0, "USD", "weekly"
    df["is_remote"], df["location"] = True, "India"
    j = J.df_to_jobs(df, "linkedin")[0]
    assert (j.salary_min, j.salary_max, j.salary_period) == (52000.0, 104000.0, "year")
    assert j.remote_type == RemoteType.REMOTE and j.remote_scope == ["IN"]


def test_relevance_filter():
    df = _df("linkedin").head(3).copy()
    df.loc[0, "location"] = "San Francisco, CA"                  # abroad onsite -> dropped
    df.loc[1, "location"], df.loc[1, "is_remote"] = "Remote, Worldwide", True   # worldwide -> kept
    df.loc[2, "location"], df.loc[2, "is_remote"] = "Remote - US only", True   # US-only -> dropped
    keep = [J.is_relevant(j) for j in J.df_to_jobs(df, "linkedin")]
    assert keep == [False, True, False]


# ------------------------------------------------------------------ jobspy search()
def test_search_happy_path_budget_and_kwargs(fake, no_sleep):
    f = fake(frames=[_df("naukri"), _df("naukri")])
    r = REGISTRY["jobspy_naukri"](ctx(queries=["a", "b"], locations=["Bengaluru", "Remote India"],
                                      budgets={"jobspy_naukri": 2}, limit=10))
    assert r.calls == 2 and len(f.calls) == 2
    assert len(r.jobs) == 5                       # second call returned the same ids -> deduped
    kw = f.calls[0]
    assert kw["site_name"] == ["naukri"] and kw["results_wanted"] == 10 and kw["hours_old"] == 72
    assert kw["fetch_description"] is True
    assert f.calls[1]["location"] == "India" and f.calls[1]["is_remote"] is True
    assert len(no_sleep) == 1 and 3 <= no_sleep[0] <= 7    # polite delay between naukri calls


def test_linkedin_kwargs(fake):
    f = fake(frames=[_df("linkedin")])
    r = J.search_linkedin(ctx(limit=100))
    assert len(r.jobs) == 5 and not r.errors
    kw = f.calls[0]
    assert kw["fetch_description"] is False and kw["results_wanted"] == 30
    assert "linkedin_fetch_description" not in kw


def test_indeed_country(fake):
    f = fake(frames=[_df("indeed")])
    J.search_indeed(ctx())
    assert f.calls[0]["country_indeed"] == "India" and f.calls[0]["fetch_description"] is True


def test_title_exclude_applies(fake):
    fake(frames=[_df("naukri")])
    r = J.search_naukri(ctx(title_exclude=["lead"]))
    assert r.jobs and all("lead" not in j.title.lower() for j in r.jobs)


def test_stop_on_406(fake):
    f = fake(frames=[_df("naukri").head(2)], errors=["response status code 406 (recaptcha required): stop"])
    r = J.search_naukri(ctx(queries=["a", "b", "c"], budgets={"jobspy_naukri": 5}))
    assert len(f.calls) == 1 and r.calls == 1
    assert len(r.jobs) == 2
    assert any("406" in e for e in r.errors) and any("stopped" in e for e in r.errors)


def test_stop_on_429_exception(fake):
    f = fake(exc=RuntimeError("429 Client Error: Too Many Requests"))
    r = J.search_linkedin(ctx(queries=["a", "b"]))
    assert len(f.calls) == 1 and r.jobs == [] and r.errors


def test_non_blocking_error_continues(fake):
    f = fake(frames=[pd.DataFrame(columns=_df("indeed").columns), _df("glassdoor")],
             errors=["location 'Atlantis' not found", None])
    r = J.search_glassdoor(ctx(locations=["Atlantis", "Bengaluru"]))
    assert len(f.calls) == 2 and len(r.jobs) == 5
    assert len(r.errors) == 1 and "Atlantis" in r.errors[0]


def test_empty_board_and_no_queries(fake):
    fake()
    r = J.search_indeed(ctx())
    assert r.jobs == [] and r.errors == [] and r.calls == 1
    r = J.search_indeed(ctx(queries=[]))
    assert r.calls == 0 and r.errors == ["no queries"]


def test_search_never_raises(monkeypatch):
    monkeypatch.setattr(J, "df_to_jobs", lambda *a: (_ for _ in ()).throw(ValueError("boom")))
    import jobspy
    monkeypatch.setattr(jobspy, "scrape_jobs", FakeScrape(frames=[_df("indeed")]))
    r = J.search_indeed(ctx())
    assert r.jobs == [] and any("boom" in e for e in r.errors)


# ------------------------------------------------------------------ serpapi
def _page(n: int) -> dict:
    return json.loads((FIX / "serpapi" / f"google_jobs_page{n}.json").read_text(encoding="utf-8"))


def test_pick_apply_url():
    p1 = _page(1)["jobs_results"]
    assert S.pick_apply_url(p1[0]["apply_options"]).startswith("https://job-boards.greenhouse.io/acmeailabs")
    assert "naukri.com" in S.pick_apply_url(p1[1]["apply_options"])        # only boards -> first
    assert S.pick_apply_url(p1[2]["apply_options"]).startswith("https://careers.nimbusrobotics")
    assert "lever.co" in S.pick_apply_url(p1[3]["apply_options"])          # auto ATS beats workday
    assert S.pick_apply_url([]) is None


def test_serp_parse_results():
    jobs, token = S.parse_results(_page(1))
    assert len(jobs) == 5 and token.startswith("eyJ")
    a, b, c, d, e = jobs
    assert a.source == "serpapi" and a.company == "Acme AI Labs" and a.location_tier == 2
    assert a.channel == Channel.AUTO and a.ats_type == "greenhouse" and a.ats_slug == "acmeailabs"
    assert a.salary_min == 1_200_000 and a.salary_max == 1_800_000 and a.salary_currency == "INR"
    assert a.exp_min == 1 and a.exp_max == 3 and "RAG pipelines" in a.jd_text
    assert len(a.raw["apply_options"]) == 3 and a.posted_at is not None
    assert a.source_url.startswith("https://www.google.com/search")
    assert b.channel == Channel.MANUAL_LINKEDIN_NAUKRI
    assert c.channel == Channel.AUTO_PROBE and c.location_tier == 1
    assert d.remote_type == RemoteType.REMOTE and "WORLDWIDE" in d.remote_scope and d.channel == Channel.AUTO
    assert d.salary_currency == "USD" and d.salary_max == 80_000
    assert e.location_tier == 4
    assert S.parse_results(None) == ([], None)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-secret-key")
    return "test-secret-key"


def test_serp_no_key(monkeypatch):
    monkeypatch.setattr(S, "env", lambda name, default=None: None)
    r = S.search(ctx())
    assert r.errors == [] and r.calls == 0 and r.jobs == []      # optional source: silently off


@respx.mock
def test_serp_search_paginates_filters(key):
    route = respx.get(S.ENDPOINT).mock(side_effect=[httpx.Response(200, json=_page(1)),
                                                     httpx.Response(200, json=_page(2))])
    r = S.search(ctx(queries=["AI Engineer"], locations=["Bengaluru"], title_exclude=["manager"]))
    assert r.calls == 2 and route.call_count == 2 and not r.errors
    first, second = route.calls[0].request.url.params, route.calls[1].request.url.params
    assert first["engine"] == "google_jobs" and first["gl"] == "in" and first["hl"] == "en"
    assert first["q"] == "AI Engineer Bengaluru" and first["location"] == "India"
    assert "next_page_token" not in first and second["next_page_token"].startswith("eyJ")
    titles = [j.title for j in r.jobs]
    assert "Machine Learning Engineer" not in titles            # San Francisco onsite filtered
    assert "Senior Manager, AI Programs" not in titles          # title exclude
    assert "LLM Engineer (Remote)" in titles                    # remote worldwide kept
    keka = next(j for j in r.jobs if j.company == "Hooli India")
    assert keka.channel == Channel.AUTO_PROBE and keka.ats_type == "keka"


@respx.mock
def test_serp_budget(key):
    route = respx.get(S.ENDPOINT).mock(return_value=httpx.Response(200, json=_page(1)))
    r = S.search(ctx(queries=["a", "b", "c"], locations=["Bengaluru"], budgets={"serpapi": 2}))
    assert r.calls == 2 and route.call_count == 2


@respx.mock
def test_serp_500_recorded_no_key_leak(key):
    respx.get(S.ENDPOINT).mock(return_value=httpx.Response(500, json={"error": "Internal"}))
    r = S.search(ctx(queries=["a"], locations=["Remote India"]))
    assert r.jobs == [] and r.errors and "500" in r.errors[0]
    assert all(key not in e for e in r.errors)


@respx.mock
def test_serp_api_error_and_no_results(key):
    respx.get(S.ENDPOINT).mock(side_effect=[
        httpx.Response(200, json={"error": "Google hasn't returned any results for this query."}),
        httpx.Response(401, json={"error": "Invalid API key."})])
    r = S.search(ctx(queries=["a", "b"], locations=[None]))
    assert r.calls == 2 and len(r.errors) == 1 and "401" in r.errors[0] and "Invalid API key" in r.errors[0]


@respx.mock
def test_serp_transport_error_never_raises(key):
    respx.get(S.ENDPOINT).mock(side_effect=httpx.ConnectError("down"))
    r = S.search(ctx())
    assert r.jobs == [] and r.errors and "ConnectError" in r.errors[0]


# ------------------------------------------------------------------ verifier regressions
def test_spread_pairs_covers_every_query_first():
    qs, locs = ["q1", "q2", "q3"], ["Indore", "Bengaluru"]
    pairs = J.spread_pairs(qs, locs)
    assert sorted(pairs) == sorted((q, loc) for q in qs for loc in locs)     # every pair exactly once
    assert [q for q, _ in pairs[:3]] == qs                                   # round 0 reaches every query
    assert {loc for _, loc in pairs[:3]} == set(locs)                        # ...and spreads locations
    assert J.spread_pairs(["a"], []) == [("a", None)]


def test_small_budget_reaches_every_query(fake):
    f = fake()
    J.search_indeed(ctx(queries=["a", "b", "c"], locations=["Indore", "Bengaluru", "Pune", "Noida"],
                        budgets={"jobspy_indeed": 3}))
    assert [c["search_term"] for c in f.calls] == ["a", "b", "c"]


def test_glassdoor_fetches_descriptions_naukri_capped(fake):
    f = fake(frames=[_df("glassdoor")])
    J.search_glassdoor(ctx(limit=50))
    assert f.calls[0]["fetch_description"] is True and f.calls[0]["country_indeed"] == "India"
    f = fake(frames=[_df("naukri")])
    J.search_naukri(ctx(limit=50))
    assert f.calls[0]["results_wanted"] == 10           # one detail request per job -> small cap


def test_remote_location_per_site(fake):
    f = fake()
    J.search_linkedin(ctx(queries=["LLM engineer"], locations=["Remote"]))
    kw = f.calls[0]
    assert kw["search_term"] == "LLM engineer remote" and kw["location"] == "India" and "is_remote" not in kw
    f = fake()
    J.search_indeed(ctx(queries=["LLM engineer"], locations=["Remote"]))
    kw = f.calls[0]
    assert kw["search_term"] == "LLM engineer" and kw["location"] is None and kw["is_remote"] is True


def _indeed_state_df(loc="KA, India", remote=False):
    df = _df("indeed").head(1).copy()
    df["location"], df["is_remote"] = loc, remote
    return df


def test_indeed_state_only_location_gets_searched_city(fake):
    fake(frames=[_indeed_state_df()])
    j = J.search_indeed(ctx(locations=["Bangalore"])).jobs[0]
    assert j.location == "KA, India (near Bengaluru)" and j.location_tier == 2
    fake(frames=[_indeed_state_df("MP, India")])
    assert J.search_indeed(ctx(locations=["Indore"])).jobs[0].location_tier == 1
    # no hint for remote searches, for city-bearing locations or without a searched city
    fake(frames=[_indeed_state_df(remote=True)])
    assert "near" not in J.search_indeed(ctx(locations=["Remote India"])).jobs[0].location
    j = J.df_to_jobs(_indeed_state_df("Pune, MH, India"), "indeed", near="Bengaluru")[0]
    assert j.location == "Pune, MH, India" and j.location_tier == 3
    assert J.df_to_jobs(_indeed_state_df(), "indeed")[0].location_tier == 3


def test_missing_location_on_india_sites():
    df = _df("glassdoor").head(1).copy()
    df["location"], df["is_remote"] = None, True
    j = J.df_to_jobs(df, "glassdoor")[0]
    assert j.location == "India" and j.location_tier == 3 and j.remote_scope == ["IN"]
    df = _df("linkedin").head(1).copy()
    df["location"] = None
    j = J.df_to_jobs(df, "linkedin")[0]
    assert j.location is None and j.location_tier is None and J.is_relevant(j)


def test_remote_without_scope_but_foreign_location_dropped():
    df = _df("linkedin").head(2).copy()
    df["is_remote"] = True
    df.loc[0, "location"], df.loc[1, "location"] = "San Francisco, CA", "Remote"
    a, b = J.df_to_jobs(df, "linkedin")
    assert a.remote_type == RemoteType.REMOTE and not a.remote_scope and not J.is_relevant(a)
    assert b.remote_type == RemoteType.REMOTE and J.is_relevant(b)


def test_row_edge_cases_unicode_huge_missing():
    rows = pd.DataFrame([
        {"id": "x1", "title": "AI इंजीनियर — GenAI 🚀", "job_url": "https://in.indeed.com/viewjob?jk=x1",
         "company": "Café Ünïcode", "location": "Bengaluru, KA, India", "description": "<p>" + "Python " * 40000 + "</p>"},
        {"id": "x2", "title": "Only title", "job_url": "https://in.indeed.com/viewjob?jk=x2"},
        {"id": "x3", "title": None, "job_url": "https://in.indeed.com/viewjob?jk=x3"},
        {"id": "x4", "title": "No url", "job_url": None},
    ])
    jobs = J.df_to_jobs(rows, "indeed")
    assert [j.source_job_id for j in jobs] == ["x1", "x2"]
    a, b = jobs
    assert a.title.startswith("AI इंजीनियर") and a.company == "Café Ünïcode" and a.location_tier == 2
    assert 0 < len(a.jd_text) <= 60000
    assert b.company == "Unknown" and b.jd_text == "" and b.posted_at is None and b.salary_min is None
    assert b.location == "India" and b.channel == Channel.MANUAL_OTHER


def test_invalid_argument_error_does_not_stop_site(fake):
    f = fake(exc=ValueError("Invalid country_indeed: 'Atlantis'"))
    r = J.search_indeed(ctx(queries=["a", "b"]))
    assert len(f.calls) == 2 and len(r.errors) == 2 and not any("stopped" in e for e in r.errors)


def test_403_blocked_stops_site(fake):
    f = fake(frames=[pd.DataFrame(columns=_df("indeed").columns)] * 2,
             errors=["response status code 403 (blocked: use a proxy)"])
    r = J.search_glassdoor(ctx(queries=["a", "b"]))
    assert len(f.calls) == 1 and any("stopped" in e for e in r.errors)


def test_serp_usd_salary_range():
    item = dict(_page(1)["jobs_results"][3])
    item["detected_extensions"] = {"salary": "US$80K–US$120K a year", "work_from_home": True}
    j = S.result_to_job(item)
    assert (j.salary_min, j.salary_max, j.salary_currency) == (80_000, 120_000, "USD")


def test_serp_bad_item_skipped():
    data = _page(1)
    data["jobs_results"][0]["location"] = {"not": "a string"}
    data["jobs_results"].insert(0, "garbage")
    jobs, _ = S.parse_results(data)
    assert len(jobs) == 4


@respx.mock
def test_serp_malformed_replies_recorded(key):
    respx.get(S.ENDPOINT).mock(side_effect=[
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(404),
        httpx.Response(200, json=_page(2))])
    r = S.search(ctx(queries=["a", "b", "c", "d"], locations=["Bengaluru"]))
    assert r.calls == 4 and len(r.errors) == 3
    assert "JSONDecodeError" in r.errors[0] and "list" in r.errors[1] and "404" in r.errors[2]
    assert r.jobs and all(key not in e for e in r.errors)
