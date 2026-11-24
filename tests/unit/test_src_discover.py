import csv
import json
from pathlib import Path

import httpx
import pytest
import respx

from jobhunt.sources import discover as D
from jobhunt.util import http as H

FX = Path(__file__).resolve().parents[1] / "fixtures" / "discover"
SEEDS = Path(__file__).resolve().parents[2] / "data" / "companies" / "seeds"
INC, EXC = D.DEFAULT_INCLUDE, D.DEFAULT_EXCLUDE


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr(H.Http.request.retry, "sleep", lambda s: None)
    H._last_hit.clear()


_CLIENT: list = []


def _http() -> H.Http:
    if not _CLIENT:
        _CLIENT.append(H.Http())
    return _CLIENT[0]


def fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def page(name):
    return (FX / "pages" / name).read_text(encoding="utf-8")


def probe(ats, slug):
    return D.probe_board(ats, slug, client=_http(), include=INC, exclude=EXC)


# ------------------------------------------------------------------ probe_board per ATS
@respx.mock
def test_greenhouse_board_counts_india_and_relevant():
    respx.get("https://boards-api.greenhouse.io/v1/boards/anthropic/jobs").mock(
        return_value=httpx.Response(200, json=fx("greenhouse_anthropic.json")))
    r = probe("greenhouse", "anthropic")
    assert r["live"] is True and r["error"] is None
    assert r["n_jobs"] == 5 and r["n_india"] == 3 and r["n_relevant"] == 3
    assert r["company"] == "Anthropic"
    assert r["sample_titles"][0].startswith("Applied AI Architect [Bangalore")


@respx.mock
def test_greenhouse_bad_slug_is_not_live():
    respx.get("https://boards-api.greenhouse.io/v1/boards/nope/jobs").mock(
        return_value=httpx.Response(404, json={"status": 404, "error": "Job not found"}))
    r = probe("greenhouse", "nope")
    assert r["live"] is False and r["error"] == "not found" and r["n_jobs"] is None


@respx.mock
def test_lever_board_and_title_filter():
    respx.get(url__startswith="https://api.lever.co/v0/postings/paytm").mock(
        return_value=httpx.Response(200, json=fx("lever_paytm.json")))
    r = probe("lever", "paytm")
    assert r["live"] is True and r["n_jobs"] == 5
    assert r["n_india"] == 5            # Noida/Patna + a Dubai role also listed in Noida/Mumbai
    assert r["n_relevant"] == 0         # procurement / analytics / sales manager titles are filtered out


@respx.mock
def test_lever_tries_eu_host_before_declaring_dead():
    us = respx.get(url__startswith="https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(404, json={"ok": False}))
    eu = respx.get(url__startswith="https://api.eu.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=[]))
    r = probe("lever", "acme")
    assert us.called and eu.called
    assert r["live"] is True and r["n_jobs"] == 0 and r["calls"] == 2


@respx.mock
def test_lever_dead_on_both_hosts():
    respx.get(url__regex=r"https://api(\.eu)?\.lever\.co/v0/postings/ghost.*").mock(
        return_value=httpx.Response(404, json={"ok": False}))
    r = probe("lever", "ghost")
    assert r["live"] is False and r["calls"] == 2


@respx.mock
def test_ashby_board():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/sarvam").mock(
        return_value=httpx.Response(200, json=fx("ashby_sarvam.json")))
    r = probe("ashby", "sarvam")
    assert r["live"] is True and r["n_jobs"] == 3 and r["n_india"] == 3
    assert r["n_relevant"] == 2         # two data-scientist roles; the engagement manager is excluded


@respx.mock
def test_workable_foreign_remote_is_not_india_or_remote_ok():
    respx.get("https://apply.workable.com/api/v1/widget/accounts/huggingface").mock(
        return_value=httpx.Response(200, json=fx("workable_huggingface.json")))
    r = probe("workable", "huggingface")
    assert r["live"] is True and r["company"] == "Hugging Face" and r["n_jobs"] == 5
    assert r["n_india_or_remote"] == 0 and r["n_relevant"] == 0   # EMEA/US remote only


@respx.mock
def test_smartrecruiters_country_in_and_empty_id():
    respx.get(url__startswith="https://api.smartrecruiters.com/v1/companies/BoschGroup/postings").mock(
        return_value=httpx.Response(200, json=fx("smartrecruiters_bosch_in.json")))
    respx.get(url__startswith="https://api.smartrecruiters.com/v1/companies/nosuch/postings").mock(
        return_value=httpx.Response(200, json=fx("smartrecruiters_empty.json")))
    r = probe("smartrecruiters", "BoschGroup")
    assert r["live"] is True and r["n_india"] == 5 and r["company"] == "Bosch Group" and r["calls"] == 1
    z = probe("smartrecruiters", "nosuch")
    assert z["live"] is False and z["calls"] == 2       # 200 + totalFound 0 twice = no such company


@respx.mock
def test_recruitee_board():
    respx.get("https://aihr.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=fx("recruitee_sample.json")))
    r = probe("recruitee", "aihr")
    assert r["live"] is True and r["company"] == "AIHR" and r["n_jobs"] == 5 and r["n_india"] == 0


@respx.mock
def test_keka_liveness_only():
    respx.get("https://acme.keka.com/careers/").mock(return_value=httpx.Response(
        200, text="<html><head><title>Careers at Acme Pvt Ltd</title></head></html>"))
    r = probe("keka", "acme")
    assert r["live"] is True and r["n_jobs"] is None and r["company"] == "Acme Pvt Ltd"


@respx.mock
def test_probe_never_raises_on_server_error():
    respx.get("https://boards-api.greenhouse.io/v1/boards/boom/jobs").mock(return_value=httpx.Response(500))
    r = probe("greenhouse", "boom")
    assert r["live"] is None and "HTTPStatusError" in r["error"]


def test_unsupported_ats_and_empty_slug():
    assert D.probe_board("taleo", "x")["error"].startswith("unsupported")
    assert D.probe_board("greenhouse", "")["error"] == "empty slug"


# ------------------------------------------------------------------ location classes
@pytest.mark.parametrize("loc,country,remote,extra,expected", [
    ("Bengaluru, Karnataka", None, None, None, "india"),
    ("Indore", None, None, None, "india"),
    ("Remote - India", None, True, None, "india"),
    ("Remote", None, True, "Anywhere in the world", "remote_ok"),
    ("Remote (APAC)", None, True, None, "remote_ok"),
    ("Remote", None, True, None, "remote_unscoped"),
    ("Paris / France", None, True, None, "other"),
    ("Remote - US", None, True, None, "other"),
    ("San Francisco, CA", None, None, None, "other"),
])
def test_location_class(loc, country, remote, extra, expected):
    assert D.location_class(loc, country, is_remote=remote, extra=extra) == expected


# ------------------------------------------------------------------ careers pages
@pytest.mark.parametrize("url,fixture,expected", [
    ("https://www.observe.ai/careers/", "observeai_greenhouse_embed.html", ("greenhouse", "observeai")),
    ("https://paytm.com/careers", "paytm_lever_link.html", ("lever", "paytm")),
    ("https://www.sprinklr.com/careers/", "sprinklr_workday_link.html", ("workday", "sprinklr/careers")),
    ("https://thelevel.ai/careers", "levelai_ashby_link.html", ("ashby", "level-ai")),
])
@respx.mock
def test_career_page_ats_on_real_pages(url, fixture, expected):
    respx.get(url).mock(return_value=httpx.Response(200, text=page(fixture),
                                                    headers={"content-type": "text/html; charset=utf-8"}))
    found = D.career_page_ats(url, client=_http())
    assert found and found[0] == expected


@respx.mock
def test_career_page_follows_one_hop_to_jobs_page():
    respx.get("https://acme.example.com/").mock(return_value=httpx.Response(
        200, text='<a href="/about">About</a><a href="/join-us">Join us</a><a href="https://x.com/careers">x</a>'))
    respx.get("https://acme.example.com/join-us").mock(return_value=httpx.Response(
        200, text='<iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeco"></iframe>'
                  '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>'))
    assert D.career_page_ats("https://acme.example.com/", client=_http()) == [("greenhouse", "acmeco")]


@respx.mock
def test_career_page_error_is_recorded_not_raised():
    respx.get("https://down.example.com/careers").mock(return_value=httpx.Response(503))
    errs: list[str] = []
    assert D.career_page_ats("https://down.example.com/careers", client=_http(), errors=errs) == []
    assert errs and "503" in errs[0]


@respx.mock
def test_career_page_reads_at_most_cap(monkeypatch):
    monkeypatch.setattr(D, "MAX_PAGE_BYTES", 1000)
    big = "x" * 5000 + '<a href="https://jobs.lever.co/late">late</a>'
    respx.get("https://big.example.com/careers").mock(return_value=httpx.Response(200, text=big))
    assert D.career_page_ats("https://big.example.com/careers", client=_http(), follow_career_links=0) == []


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/embed/job_board/js?for=acme", ("greenhouse", "acme")),
    ("https://boards.greenhouse.io/embed/job_app?for=acme&token=1", ("greenhouse", "acme")),
    ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
    ("https://api.lever.co/v0/postings/acme?mode=json", ("lever", "acme")),
    ("https://jobs.eu.lever.co/acme/abc", ("lever", "acme")),
    ("https://api.ashbyhq.com/posting-api/job-board/acme", ("ashby", "acme")),
    ("https://jobs.ashbyhq.com/acme/embed", ("ashby", "acme")),
    ("https://careers.smartrecruiters.com/AcmeGroup", ("smartrecruiters", "AcmeGroup")),
    ("https://apply.workable.com/acme/j/ABC123", ("workable", "acme")),
    ("https://acme.recruitee.com/o/ml-engineer", ("recruitee", "acme")),
    ("https://acme.keka.com/careers/jobdetails/1", ("keka", "acme")),
    ("https://acme.zohorecruit.in/jobs/Careers", ("zohorecruit", "acme")),
    ("https://acme.darwinbox.in/ms/candidate/careers", ("darwinbox", "acme")),
    ("https://acme.wd5.myworkdayjobs.com/en-US/External", ("workday", "acme/External")),
    ("https://www.workable.com/pricing", ("workable", None)),
    ("https://job-boards.greenhouse.io/${company}", ("greenhouse", None)),
    ("https://example.com/careers", (None, None)),
])
def test_ats_from_url(url, expected):
    assert D.ats_from_url(url) == expected


def test_find_ats_handles_escaped_json_urls():
    html = '{"url":"https:\\/\\/jobs.lever.co\\u002Facme\\u002Fxyz"} grnh.js?for=other'
    assert D.find_ats_in_html(html) == [("greenhouse", "other"), ("lever", "acme")]


# ------------------------------------------------------------------ probe_many resume
@respx.mock
def test_probe_many_resumes_from_state(tmp_path):
    route = respx.get(url__regex=r"https://boards-api\.greenhouse\.io/v1/boards/(a|b|c)/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [], "meta": {"total": 0}}))
    rows = [{"ats": "greenhouse", "slug": s, "company": s.upper()} for s in "abc"]
    state = tmp_path / "probe_state.json"
    first = D.probe_many(rows, 2, state_path=state, client=_http(), include=INC, exclude=EXC)
    assert len(first) == 2 and route.call_count == 2
    assert first[0]["company"] == "A"
    second = D.probe_many(rows, None, state_path=state, client=_http(), include=INC, exclude=EXC)
    assert len(second) == 3 and route.call_count == 3        # only "c" was new
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert set(saved["results"]) == {"greenhouse:a", "greenhouse:b", "greenhouse:c"}
    D.probe_many(rows, None, state_path=state, client=_http(), include=INC, exclude=EXC, refresh=True)
    assert route.call_count == 6


def test_probe_many_corrupt_state_starts_fresh(tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{not json", encoding="utf-8")
    assert D.load_state(state) == {"version": 1, "results": {}}


# ------------------------------------------------------------------ seed files
def test_read_seed_csv_formats(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("name,slug,url\nAcme,acme,https://jobs.lever.co/acme\n", encoding="utf-8")
    assert D.read_seed_csv(a, "lever")[0] == {"company": "Acme", "ats": "lever", "slug": "acme",
                                              "careers_url": "https://jobs.lever.co/acme", "notes": ""}
    k = tmp_path / "k.csv"
    k.write_text("name,url\nBrewbay,https://abcoffee.keka.com/careers\n", encoding="utf-8")
    assert D.read_seed_csv(k, "keka")[0]["slug"] == "abcoffee"


def test_india_ai_seed_file_shape():
    path = SEEDS / "india_ai.csv"
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == D.SEED_COLUMNS
        rows = list(reader)
    assert len(rows) >= 100
    keys = [(r["ats"], r["slug"].lower()) for r in rows]
    assert len(keys) == len(set(keys))
    assert all(r["ats"] in D.FETCHERS for r in rows)
    assert all(r["company"] and r["slug"] for r in rows)
