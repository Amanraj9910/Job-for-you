from datetime import UTC, datetime, timedelta

import pytest

from jobhunt.classify import board_kind, classify_channel, detect_ats
from jobhunt.models import Channel, RemoteType
from jobhunt.normalize import (
    annual_inr,
    normalize_location,
    parse_experience,
    parse_posted_at,
    parse_salary,
)
from jobhunt.sources.base import title_matches

# ------------------------------------------------------------------ location
LOC_CASES = [
    ("Indore, Madhya Pradesh, India", None, None, 1, RemoteType.UNKNOWN, []),
    ("Bangalore", None, None, 2, RemoteType.UNKNOWN, []),
    ("Bengaluru Urban, Karnataka", None, None, 2, RemoteType.UNKNOWN, []),
    ("Hybrid - Pune", None, None, 3, RemoteType.HYBRID, []),
    ("Remote - India", None, None, 3, RemoteType.REMOTE, ["IN"]),
    ("Remote", None, "Anywhere in the world", 4, RemoteType.REMOTE, ["WORLDWIDE"]),
    ("Remote - US only", None, None, 4, RemoteType.REMOTE, ["US"]),
    ("Remote, EU timezones, must be EU resident", None, None, 4, RemoteType.REMOTE, ["EU"]),
    ("Anywhere", None, None, 4, RemoteType.REMOTE, ["WORLDWIDE"]),
    ("San Francisco, CA", None, None, 4, RemoteType.UNKNOWN, []),
    ("Gurgaon", None, None, 3, RemoteType.UNKNOWN, []),
    ("Remote (APAC)", None, None, 4, RemoteType.REMOTE, ["APAC"]),
    (None, None, None, None, RemoteType.UNKNOWN, []),
    ("Navi Mumbai, Maharashtra", None, None, 3, RemoteType.UNKNOWN, []),
]


@pytest.mark.parametrize("loc,country,extra,tier,rtype,scope", LOC_CASES)
def test_normalize_location(loc, country, extra, tier, rtype, scope):
    r = normalize_location(loc, country, extra_text=extra)
    assert r["location_tier"] == tier
    assert r["remote_type"] == rtype
    assert r["remote_scope"] == scope


# ------------------------------------------------------------------ experience
EXP_CASES = [
    ("We need 2-4 years of experience in Python", (2, 4)),
    ("Experience: 2 – 5 Yrs", (2, 5)),
    ("3+ years building ML systems", (3, None)),
    ("Minimum 5 years of hands-on experience", (5, None)),
    ("at least 1 year experience", (1, None)),
    ("0-2 Yrs.", (0, 2)),
    ("Freshers welcome!", (0, 1)),
    ("5 or more years in backend", (5, None)),
    ("Three years of professional experience", (3, None)),
    ("1 to 3 years exp", (1, 3)),
    ("No experience mentioned here", (None, None)),
    ("Founded 2015, we have 200 employees", (None, None)),
]


@pytest.mark.parametrize("text,expected", EXP_CASES)
def test_parse_experience(text, expected):
    assert parse_experience(text) == expected


# ------------------------------------------------------------------ salary
SAL_CASES = [
    ("8-12 LPA", (800000, 1200000, "INR", "year")),
    ("₹10L", (1000000, 1000000, "INR", "year")),
    ("10 Lakhs p.a.", (1000000, 1000000, "INR", "year")),
    ("CTC up to 12 LPA", (None, 1200000, "INR", "year")),
    ("INR 6,00,000 - 9,00,000", (600000, 900000, "INR", "year")),
    ("$120,000 - $150,000", (120000, 150000, "USD", "year")),
    ("₹50,000/month", (50000, 50000, "INR", "month")),
    ("$80k–$100k", (80000, 100000, "USD", "year")),
    ("2-4 years of experience", (None, None, None, None)),
    ("Salary: 15 – 20 LPA", (1500000, 2000000, "INR", "year")),
]


@pytest.mark.parametrize("text,expected", SAL_CASES)
def test_parse_salary(text, expected):
    assert parse_salary(text) == expected


def test_annual_inr():
    assert annual_inr(50000, "INR", "month") == 600000
    assert annual_inr(100000, "USD", "year") == 8_400_000


# ------------------------------------------------------------------ dates
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("value,expected", [
    (1761523200000, datetime(2025, 10, 27, tzinfo=UTC)),
    (1761523200, datetime(2025, 10, 27, tzinfo=UTC)),
    ("2026-10-20T10:00:00Z", datetime(2026, 10, 20, 10, tzinfo=UTC)),
    ("3 days ago", NOW - timedelta(days=3)),
    ("30+ days ago", NOW - timedelta(days=30)),
    ("Today", NOW),
    ("yesterday", NOW - timedelta(days=1)),
    ("2 weeks ago", NOW - timedelta(weeks=2)),
    ("", None),
    ("garbage text", None),
])
def test_parse_posted_at(value, expected):
    assert parse_posted_at(value, now=NOW) == expected


# ------------------------------------------------------------------ ATS detection / channels
URL_CASES = [
    ("https://boards.greenhouse.io/acme/jobs/123", Channel.AUTO, "greenhouse", "acme"),
    ("https://job-boards.greenhouse.io/acme/jobs/123", Channel.AUTO, "greenhouse", "acme"),
    ("https://job-boards.eu.greenhouse.io/acme/jobs/9", Channel.AUTO, "greenhouse", "acme"),
    ("https://acme.com/careers?gh_jid=4455", Channel.AUTO, "greenhouse", None),
    ("https://jobs.lever.co/acme/abc-123/apply", Channel.AUTO, "lever", "acme"),
    ("https://jobs.eu.lever.co/acme/abc", Channel.AUTO, "lever", "acme"),
    ("https://jobs.ashbyhq.com/acme/uuid", Channel.AUTO, "ashby", "acme"),
    ("https://jobs.smartrecruiters.com/Bosch/7438", Channel.AUTO, "smartrecruiters", "Bosch"),
    ("https://apply.workable.com/acme/j/ABC123/", Channel.AUTO, "workable", "acme"),
    ("https://acme.recruitee.com/o/ai-engineer", Channel.AUTO, "recruitee", "acme"),
    ("https://acme.bamboohr.com/careers/12", Channel.AUTO, "bamboohr", "acme"),
    ("https://acme.breezy.hr/p/123", Channel.AUTO, "breezy", "acme"),
    ("https://acme.keka.com/careers/jobdetails/123", Channel.AUTO_PROBE, "keka", "acme"),
    ("https://acme.zohorecruit.in/jobs/Careers/123", Channel.AUTO_PROBE, "zohorecruit", "acme"),
    ("https://acme.freshteam.com/jobs/abc", Channel.AUTO_PROBE, "freshteam", "acme"),
    ("https://acme.darwinbox.in/ms/candidate/careers/123", Channel.MANUAL_ACCOUNT_PORTAL, "darwinbox", None),
    ("https://acme.wd3.myworkdayjobs.com/en-US/External/job/X", Channel.MANUAL_ACCOUNT_PORTAL, "workday", None),
    ("https://career5.successfactors.eu/career?company=acme", Channel.MANUAL_ACCOUNT_PORTAL, "successfactors", None),
    ("https://acme.taleo.net/careersection/2/jobdetail.ftl", Channel.MANUAL_ACCOUNT_PORTAL, "taleo", None),
    ("https://careers-acme.icims.com/jobs/1/job", Channel.MANUAL_ACCOUNT_PORTAL, "icims", None),
    ("https://docs.google.com/forms/d/e/abc/viewform", Channel.MANUAL_ACCOUNT_PORTAL, "google_forms", None),
    ("mailto:jobs@acme.com", Channel.MANUAL_EMAIL, None, None),
    ("https://acme.com/careers/ai-engineer", Channel.AUTO_PROBE, None, None),
    # boards
    ("https://www.linkedin.com/jobs/view/123", Channel.MANUAL_LINKEDIN_NAUKRI, None, None),
    ("https://www.naukri.com/job-listings-ai-engineer-123", Channel.MANUAL_LINKEDIN_NAUKRI, None, None),
    ("https://in.indeed.com/viewjob?jk=abc", Channel.MANUAL_OTHER, None, None),
    ("https://www.glassdoor.co.in/job-listing/x", Channel.MANUAL_OTHER, None, None),
    ("https://himalayas.app/companies/acme/jobs/ai", Channel.MANUAL_OTHER, None, None),
    ("https://remotive.com/remote-jobs/ai/1", Channel.MANUAL_OTHER, None, None),
    ("https://remoteok.com/remote-jobs/1", Channel.MANUAL_OTHER, None, None),
    ("https://weworkremotely.com/remote-jobs/acme-ai", Channel.MANUAL_OTHER, None, None),
    ("https://www.foundit.in/job/ai-123", Channel.MANUAL_OTHER, None, None),
    ("https://www.instahyre.com/job-123", Channel.MANUAL_OTHER, None, None),
    ("https://cutshort.io/job/ai-123", Channel.MANUAL_OTHER, None, None),
    ("https://wellfound.com/jobs/123", Channel.MANUAL_OTHER, None, None),
    ("https://www.ycombinator.com/companies/acme/jobs/1", Channel.MANUAL_OTHER, None, None),
    ("https://www.workatastartup.com/jobs/1", Channel.MANUAL_OTHER, None, None),
    (None, Channel.MANUAL_OTHER, None, None),
]


@pytest.mark.parametrize("url,channel,ats,slug", URL_CASES)
def test_classify_channel(url, channel, ats, slug):
    ch, a, s = classify_channel(url)
    assert (ch, a, s) == (channel, ats, slug)


def test_board_never_auto():
    for url, *_ in URL_CASES:
        if board_kind(url):
            assert classify_channel(url)[0] not in (Channel.AUTO, Channel.AUTO_PROBE), url


def test_linkedin_source_without_url_is_manual():
    assert classify_channel(None, source="jobspy_linkedin")[0] == Channel.MANUAL_LINKEDIN_NAUKRI


def test_detect_ats_embed():
    assert detect_ats("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=123") == ("greenhouse", "acme")


# ------------------------------------------------------------------ title filter
@pytest.mark.parametrize("title,ok", [
    ("AI Engineer", True), ("Senior Staff ML Engineer", False), ("GenAI Developer", True),
    ("Account Executive", False), ("Software Engineer - Backend (Python)", True), ("Head of AI", False),
    ("Data Entry Operator", False),
])
def test_title_matches(title, ok):
    inc = ["ai", "ml", "genai", "software engineer", "python", "llm"]
    exc = ["staff", "principal", "head of", "account executive"]
    assert title_matches(title, inc, exc) is ok
