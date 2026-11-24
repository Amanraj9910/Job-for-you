"""ATS-core sources (greenhouse, lever, ashby, smartrecruiters) against recorded fixtures. No network."""
import copy
import json
from pathlib import Path

import httpx
import pytest
import respx

from jobhunt.models import Channel, RemoteType
from jobhunt.sources import ashby, greenhouse, lever, smartrecruiters
from jobhunt.sources.base import REGISTRY, SearchContext
from jobhunt.sources.greenhouse import location_ok
from jobhunt.util import http as H

FIX = Path(__file__).resolve().parents[1] / "fixtures"
GH = "https://boards-api.greenhouse.io/v1/boards"
LV = "https://api.lever.co/v0/postings"
LV_EU = "https://api.eu.lever.co/v0/postings"
AB = "https://api.ashbyhq.com/posting-api/job-board"
SR = "https://api.smartrecruiters.com/v1/companies"


def fx(rel: str):
    return json.loads((FIX / rel).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr(H.Http.request.retry, "sleep", lambda s: None)
    H._last_hit.clear()


_CLIENT: list = []


def ctx(**kw) -> SearchContext:
    if not _CLIENT:            # building an httpx client (SSL context) costs ~1 s on Windows: share one
        _CLIENT.append(H.Http())
    kw.setdefault("client", _CLIENT[0])
    return SearchContext(**kw)


# ------------------------------------------------------------------ mocks
def mock_greenhouse(m):
    m.get(f"{GH}/druva/jobs").mock(return_value=httpx.Response(200, json=fx("greenhouse/druva_jobs.json")))
    for f in (FIX / "greenhouse").glob("druva_job_*.json"):
        if "questions" in f.name:
            continue
        jid = f.stem.rsplit("_", 1)[-1]
        m.get(f"{GH}/druva/jobs/{jid}", params={"pay_transparency": "true"}).mock(
            return_value=httpx.Response(200, json=fx(f"greenhouse/{f.name}")))


def mock_smartrecruiters(m, company="ServiceNow"):
    m.get(f"{SR}/{company}/postings", params={"country": "in"}).mock(
        return_value=httpx.Response(200, json=fx("smartrecruiters/servicenow_postings.json")))
    m.get(f"{SR}/{company}/postings", params={"q": "remote"}).mock(
        return_value=httpx.Response(200, json=fx("smartrecruiters/servicenow_remote.json")))
    for f in (FIX / "smartrecruiters").glob("servicenow_posting_*.json"):
        pid = f.stem.rsplit("_", 1)[-1]
        m.get(f"{SR}/{company}/postings/{pid}").mock(
            return_value=httpx.Response(200, json=fx(f"smartrecruiters/{f.name}")))


# ------------------------------------------------------------------ location filter
@pytest.mark.parametrize("loc,country,remote,keep", [
    ("Pune, Maharashtra, India", None, None, True),
    ("Bangalore", "IN", None, True),
    ("Hyderabad, , India", "in", None, True),
    ("Worldwide", None, None, True),
    ("Remote, Global", None, None, True),
    ("Remote, APAC", None, None, True),
    ("Remote - US or APAC", None, None, True),
    ("Remote", None, None, True),
    ("Remote or hybrid", None, None, True),
    ("Multiple Locations", None, None, True),
    (None, None, None, True),
    ("Remote, UK", None, None, False),
    ("Remote, EMEA", None, None, False),
    ("Remote, AMER", None, None, False),
    ("Remote, San Francisco, CA", None, True, False),
    ("Remote", "US", True, False),
    ("East Coast", "US", True, False),
    ("Santa Clara, CA", None, None, False),
    ("New York, NY", None, None, False),
    ("Basking Ridge, New Jersey", None, None, False),
    ("Remote - Japan", None, True, False),
    ("London", None, None, False),
])
def test_location_ok(loc, country, remote, keep):
    assert location_ok(loc, country, is_remote=remote) is keep


# ------------------------------------------------------------------ greenhouse
@respx.mock(assert_all_called=False)
def test_greenhouse_board_parses_and_filters(respx_mock):
    mock_greenhouse(respx_mock)
    res = REGISTRY["greenhouse"](ctx(boards={"greenhouse": ["druva"]}))
    assert res.errors == []
    ids = {j.source_job_id for j in res.jobs}
    # Santa Clara and "Remote, UK" dropped before any detail call; Worldwide kept
    assert ids == {"druva:8883093002", "druva:8857583002", "druva:7679725002"}
    assert res.calls == 1 + 3
    j = next(j for j in res.jobs if j.source_job_id == "druva:8883093002")
    assert j.title == "Professional Services Consultant"
    assert j.company == "Druva"
    assert j.source == "greenhouse" and j.ats_type == "greenhouse" and j.ats_slug == "druva"
    assert j.apply_url == j.source_url and "gh_jid=8883093002" in j.apply_url
    assert j.location == "Pune, Maharashtra, India" and j.country == "IN" and j.location_tier == 3
    assert j.posted_at is not None and j.posted_at.year == 2026
    assert j.department == "Professional Services"
    assert "Druva" in j.jd_text and "&lt;" not in j.jd_text and "<p>" not in j.jd_text
    assert j.channel == Channel.AUTO
    assert j.raw == {"id": 8883093002, "internal_job_id": 6557435002, "requisition_id": "2902"}


@respx.mock(assert_all_called=False)
def test_greenhouse_title_filter_skips_detail_calls(respx_mock):
    mock_greenhouse(respx_mock)
    res = greenhouse.search(ctx(boards={"greenhouse": ["druva"]}, title_include=["engineer"],
                                title_exclude=["sales"]))
    assert res.jobs == [] and res.errors == []
    assert res.calls == 1
    res = greenhouse.search(ctx(boards={"greenhouse": ["druva"]}, title_include=["consultant", "engineer"]))
    assert [j.title for j in res.jobs] == ["Professional Services Consultant", "Sales Engineer"]
    assert res.calls == 3


@respx.mock(assert_all_called=False)
def test_greenhouse_limit_and_budget(respx_mock):
    mock_greenhouse(respx_mock)
    res = greenhouse.search(ctx(boards={"greenhouse": ["druva"]}, limit=1))
    assert len(res.jobs) == 1 and res.calls == 2
    res = greenhouse.search(ctx(boards={"greenhouse": ["druva", "druva2"]}, budgets={"greenhouse": 2}))
    assert len(res.jobs) == 1 and res.calls == 2
    assert any("budget_exhausted" in e for e in res.errors)


@respx.mock
def test_greenhouse_bad_token_is_recorded(respx_mock):
    respx_mock.get(f"{GH}/nope/jobs").mock(return_value=httpx.Response(404, json=fx("greenhouse/not_found.json")))
    res = greenhouse.search(ctx(boards={"greenhouse": ["nope"]}))
    assert res.jobs == [] and res.calls == 1
    assert res.errors and "not_found" in res.errors[0]
    with pytest.raises(greenhouse.SourceError):
        greenhouse.fetch_board("nope", ctx())


@respx.mock(assert_all_called=False)
def test_greenhouse_500_never_raises_and_other_boards_continue(respx_mock):
    respx_mock.get(f"{GH}/broken/jobs").mock(return_value=httpx.Response(500))
    mock_greenhouse(respx_mock)
    res = greenhouse.search(ctx(boards={"greenhouse": ["broken", "druva"]}, title_include=["consultant"]))
    assert any("broken" in e and "500" in e for e in res.errors)
    assert [j.source_job_id for j in res.jobs] == ["druva:8883093002"]
    assert res.ok


@respx.mock(assert_all_called=False)
def test_greenhouse_detail_failure_is_per_job(respx_mock):
    respx_mock.get(f"{GH}/druva/jobs/8883093002").mock(return_value=httpx.Response(500))
    mock_greenhouse(respx_mock)
    res = greenhouse.search(ctx(boards={"greenhouse": ["druva"]}, title_include=["consultant", "engineer"]))
    assert [j.title for j in res.jobs] == ["Sales Engineer"]
    assert len(res.errors) == 1 and "8883093002" in res.errors[0]


def test_greenhouse_pay_ranges():
    item = fx("greenhouse/druva_jobs.json")["jobs"][0]
    detail = fx("greenhouse/druva_job_8883093002.json")
    detail["pay_input_ranges"] = [{"min_cents": 250000000, "max_cents": 400000000, "currency_type": "INR",
                                   "title": "Annual CTC", "blurb": ""}]
    j = greenhouse.parse_job("druva", item, detail)
    assert (j.salary_min, j.salary_max, j.salary_currency, j.salary_period) == (2_500_000, 4_000_000, "INR", "year")


@respx.mock
def test_greenhouse_fetch_questions(respx_mock):
    respx_mock.get(f"{GH}/druva/jobs/8883093002", params={"questions": "true"}).mock(
        return_value=httpx.Response(200, json=fx("greenhouse/druva_job_8883093002_questions.json")))
    qs = greenhouse.fetch_questions("druva", 8883093002, ctx())
    assert set(qs[0]) >= {"label", "name", "type", "required", "options"}
    by_name = {q["name"]: q for q in qs}
    assert by_name["first_name"] == {**by_name["first_name"], "label": "First Name", "type": "input_text",
                                     "required": True, "options": []}
    assert by_name["resume"]["type"] == "input_file" and by_name["resume_text"]["type"] == "textarea"
    assert by_name["cover_letter"]["required"] is False
    selects = [q for q in qs if q["type"] == "multi_value_single_select"]
    assert selects and [o["label"] for o in selects[0]["options"]] == ["Yes", "No"]
    assert all("\n" not in q["label"] for q in qs)
    assert by_name["location"]["section"] == "location"


@respx.mock
def test_greenhouse_fetch_questions_gone(respx_mock):
    respx_mock.get(f"{GH}/druva/jobs/1").mock(return_value=httpx.Response(404))
    with pytest.raises(greenhouse.SourceError):
        greenhouse.fetch_questions("druva", 1, ctx())


# ------------------------------------------------------------------ lever
@respx.mock
def test_lever_board_parses_and_filters(respx_mock):
    respx_mock.get(f"{LV}/zeta", params={"mode": "json"}).mock(
        return_value=httpx.Response(200, json=fx("lever/zeta.json")))
    res = REGISTRY["lever"](ctx(boards={"lever": ["zeta"]}))
    assert res.errors == [] and res.calls == 1
    # Basking Ridge (US), East Coast (US remote) and USA (remote) are dropped
    assert [j.title for j in res.jobs] == ["Cloud Network Engineer II", "Corporate Risk and Compliance - Associate II"]
    j = res.jobs[0]
    assert j.source == "lever" and j.source_job_id == "zeta:dad16204-8051-4517-9e50-966f329045b5"
    assert j.company == "Zeta"
    assert j.source_url == "https://jobs.lever.co/zeta/dad16204-8051-4517-9e50-966f329045b5"
    assert j.apply_url == j.source_url + "/apply"
    assert j.location == "Hyderabad" and j.country == "IN" and j.location_tier == 3
    assert j.remote_type == RemoteType.ONSITE
    assert j.employment_type == "Full-time" and j.department == "Engineering"
    assert j.posted_at is not None and j.posted_at.year == 2026
    assert len(j.jd_text) > 500 and "<li>" not in j.jd_text
    assert j.channel == Channel.AUTO and j.ats_type == "lever" and j.ats_slug == "zeta"
    assert res.jobs[1].location_tier == 2
    assert j.raw == {"id": "dad16204-8051-4517-9e50-966f329045b5"}


@respx.mock
def test_lever_eu_fallback(respx_mock):
    respx_mock.get(f"{LV}/zeta").mock(return_value=httpx.Response(404, json=fx("lever/not_found.json")))
    respx_mock.get(f"{LV_EU}/zeta").mock(return_value=httpx.Response(200, json=fx("lever/zeta.json")))
    res = lever.search(ctx(boards={"lever": ["zeta"]}, title_include=["engineer"]))
    assert [j.title for j in res.jobs] == ["Cloud Network Engineer II"]
    assert res.calls == 2 and res.errors == []


@respx.mock
def test_lever_bad_site_tries_both_hosts(respx_mock):
    a = respx_mock.get(f"{LV}/nope").mock(return_value=httpx.Response(404, json=fx("lever/not_found.json")))
    b = respx_mock.get(f"{LV_EU}/nope").mock(return_value=httpx.Response(404, json=fx("lever/not_found.json")))
    res = lever.search(ctx(boards={"lever": ["nope"]}))
    assert a.called and b.called
    assert res.jobs == [] and "not_found" in res.errors[0]


@respx.mock
def test_lever_empty_board_is_not_an_error(respx_mock):
    respx_mock.get(f"{LV}/quiet").mock(return_value=httpx.Response(200, json=[]))
    res = lever.search(ctx(boards={"lever": ["quiet"]}))
    assert res.jobs == [] and res.errors == []


@respx.mock
def test_lever_500_never_raises(respx_mock):
    respx_mock.get(f"{LV}/zeta").mock(return_value=httpx.Response(500))
    res = lever.search(ctx(boards={"lever": ["zeta"]}))
    assert res.jobs == [] and res.errors and "500" in res.errors[0]


def test_lever_salary_range_and_remote_india():
    p = copy.deepcopy(fx("lever/zeta.json")[0])
    p["salaryRange"] = {"min": 1800000, "max": 2600000, "currency": "INR", "interval": "per-year-salary"}
    p["workplaceType"] = "remote"
    p["categories"] = {**p["categories"], "location": "Remote - India", "allLocations": ["Remote - India"]}
    j = lever.parse_posting("zeta", p)
    assert (j.salary_min, j.salary_max, j.salary_currency, j.salary_period) == (1_800_000, 2_600_000, "INR", "year")
    assert j.remote_type == RemoteType.REMOTE and j.remote_scope == ["IN"] and j.location_tier == 3


# ------------------------------------------------------------------ ashby
@respx.mock
def test_ashby_board_parses_and_filters(respx_mock):
    respx_mock.get(f"{AB}/supabase", params={"includeCompensation": "true"}).mock(
        return_value=httpx.Response(200, json=fx("ashby/supabase.json")))
    respx_mock.get(f"{AB}/sarvam").mock(return_value=httpx.Response(200, json=fx("ashby/sarvam.json")))
    respx_mock.get(f"{AB}/linear").mock(return_value=httpx.Response(200, json=fx("ashby/linear.json")))
    res = REGISTRY["ashby"](ctx(boards={"ashby": ["supabase", "sarvam", "linear"]}))
    assert res.errors == [] and res.calls == 3
    titles = [j.title for j in res.jobs]
    # supabase: Global + APAC kept; EMEA, AMER, Bay Area dropped. linear: Europe / North America dropped
    assert titles == ["AI Platform Engineer", "Support Engineer (APAC)", "Engagement Manager, Chanakya",
                      "Data Scientist - Evaluations, Chanakya", "Embedded Data Scientist, Chanakya"]
    g = res.jobs[0]
    assert g.source == "ashby" and g.company == "Supabase" and g.source_job_id.startswith("supabase:")
    assert g.remote_type == RemoteType.REMOTE and g.remote_scope == ["WORLDWIDE"]
    assert g.source_url.startswith("https://jobs.ashbyhq.com/supabase/")
    assert g.apply_url == g.source_url + "/application"
    assert g.channel == Channel.AUTO and g.ats_type == "ashby" and g.ats_slug == "supabase"
    assert g.posted_at is not None and len(g.jd_text) > 500 and "<p" not in g.jd_text
    assert res.jobs[1].remote_scope == ["APAC"]
    s = res.jobs[3]
    assert s.location == "Bengaluru" and s.country == "IN" and s.location_tier == 2
    assert s.remote_type == RemoteType.ONSITE and s.employment_type == "FullTime"


def test_ashby_compensation():
    j = next(x for x in fx("ashby/supabase.json")["jobs"] if x["compensation"]["compensationTierSummary"])
    job = ashby.parse_job("supabase", j)
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (200000, 270000, "USD", "year")
    j2 = copy.deepcopy(j)
    j2["compensation"]["summaryComponents"] = []
    job2 = ashby.parse_job("supabase", j2)   # falls back to the summary text
    assert (job2.salary_min, job2.salary_max, job2.salary_currency) == (200000, 270000, "USD")


@respx.mock
def test_ashby_secondary_location_in_india_is_kept(respx_mock):
    data = fx("ashby/linear.json")
    data["jobs"][0]["secondaryLocations"] = [{"location": "Bengaluru", "address": {}}]
    respx_mock.get(f"{AB}/linear").mock(return_value=httpx.Response(200, json=data))
    res = ashby.search(ctx(boards={"ashby": ["linear"]}))
    assert len(res.jobs) == 1 and res.jobs[0].location == "Europe; Bengaluru" and res.jobs[0].location_tier == 2


@respx.mock
def test_ashby_bad_name_and_500(respx_mock):
    respx_mock.get(f"{AB}/nope").mock(return_value=httpx.Response(404, text="Not Found"))
    respx_mock.get(f"{AB}/broken").mock(return_value=httpx.Response(500))
    res = ashby.search(ctx(boards={"ashby": ["nope", "broken"]}))
    assert res.jobs == [] and len(res.errors) == 2
    assert "not_found" in res.errors[0] and "500" in res.errors[1]


@respx.mock
def test_ashby_title_filter(respx_mock):
    respx_mock.get(f"{AB}/sarvam").mock(return_value=httpx.Response(200, json=fx("ashby/sarvam.json")))
    res = ashby.search(ctx(boards={"ashby": ["sarvam"]}, title_include=["data scientist"],
                           title_exclude=["embedded"]))
    assert [j.title for j in res.jobs] == ["Data Scientist - Evaluations, Chanakya"]


# ------------------------------------------------------------------ smartrecruiters
@respx.mock(assert_all_called=False)
def test_smartrecruiters_board_parses_and_filters(respx_mock):
    mock_smartrecruiters(respx_mock)
    res = REGISTRY["smartrecruiters"](ctx(boards={"smartrecruiters": ["ServiceNow"]}, title_include=["engineer"]))
    assert res.errors == []
    # 2 list calls (India + remote pass) + 3 details; US-only remote postings dropped without detail calls
    assert res.calls == 5
    assert [j.source_job_id for j in res.jobs] == ["servicenow:744000157740014", "servicenow:744000157729739",
                                                   "servicenow:744000157722179"]
    j = res.jobs[0]
    assert j.title == "Software Engineer" and j.company == "ServiceNow" and j.source == "smartrecruiters"
    assert j.location == "Hyderabad, India" and j.country == "IN" and j.location_tier == 3
    assert j.apply_url.startswith("https://jobs.smartrecruiters.com/ServiceNow/744000157740014")
    assert j.channel == Channel.AUTO and j.ats_type == "smartrecruiters"
    assert j.posted_at is not None and j.department and j.employment_type == "Full-time"
    assert "Qualifications" in j.jd_text and "<p>" not in j.jd_text
    assert res.jobs[1].remote_type == RemoteType.HYBRID
    assert set(j.raw) == {"id", "uuid", "refNumber"}


@respx.mock
def test_smartrecruiters_unknown_company_is_empty_or_unknown(respx_mock):
    route = respx_mock.get(f"{SR}/NoSuchCo/postings").mock(
        return_value=httpx.Response(200, json=fx("smartrecruiters/empty.json")))
    res = smartrecruiters.search(ctx(boards={"smartrecruiters": ["NoSuchCo"]}))
    assert res.jobs == [] and route.call_count == 2
    assert res.errors and "empty_or_unknown" in res.errors[0]


@respx.mock
def test_smartrecruiters_fallback_apply_url_and_pagination(respx_mock):
    page = fx("smartrecruiters/servicenow_postings.json")
    item = copy.deepcopy(page["content"][0])
    full = {"offset": 0, "limit": 100, "totalFound": 101, "content": [item] * 100}
    second = {"offset": 100, "limit": 100, "totalFound": 101, "content": []}
    lst = respx_mock.get(f"{SR}/Acme/postings").mock(side_effect=[
        httpx.Response(200, json=full), httpx.Response(200, json=second),
        httpx.Response(200, json=fx("smartrecruiters/empty.json"))])
    detail = fx("smartrecruiters/servicenow_posting_744000157740014.json")
    for k in ("applyUrl", "postingUrl"):
        detail.pop(k)
    respx_mock.get(f"{SR}/Acme/postings/744000157740014").mock(return_value=httpx.Response(200, json=detail))
    res = smartrecruiters.search(ctx(boards={"smartrecruiters": ["Acme"]}))
    assert lst.call_count == 3
    assert lst.calls[1].request.url.params["offset"] == "100"
    assert len(res.jobs) == 1   # duplicates collapsed
    assert res.jobs[0].apply_url == "https://jobs.smartrecruiters.com/Acme/744000157740014"
    assert res.jobs[0].channel == Channel.AUTO


@respx.mock
def test_smartrecruiters_500_never_raises(respx_mock):
    respx_mock.get(f"{SR}/ServiceNow/postings").mock(return_value=httpx.Response(500))
    res = smartrecruiters.search(ctx(boards={"smartrecruiters": ["ServiceNow"]}))
    assert res.jobs == [] and res.errors and "500" in res.errors[0]


# ------------------------------------------------------------------ registry / contract
@pytest.mark.parametrize("name", ["greenhouse", "lever", "ashby", "smartrecruiters"])
def test_no_boards_is_empty_and_ok(name):
    res = REGISTRY[name](ctx())
    assert res.source == name and res.jobs == [] and res.errors == [] and res.calls == 0


@pytest.mark.parametrize("name", ["greenhouse", "lever", "ashby", "smartrecruiters"])
def test_bad_boards_config_never_raises(name):
    res = REGISTRY[name](ctx(boards=None))
    assert res.jobs == [] and res.errors


@pytest.mark.parametrize("name", ["greenhouse", "lever", "ashby", "smartrecruiters"])
def test_transport_error_never_raises(name):
    with respx.mock:
        respx.route().mock(side_effect=httpx.ConnectError("offline"))
        res = REGISTRY[name](ctx(boards={name: ["x"]}))
    assert res.jobs == [] and res.errors and "ConnectError" in res.errors[0]
