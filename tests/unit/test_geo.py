"""Profile-driven geography: the same code works for users in any supported country."""
import pytest

from jobhunt import geo
from jobhunt.geo import Geo, default_ok_scopes, foreign_hint, is_home, remote_scope
from jobhunt.match.gates import run_gates
from jobhunt.models import Job
from jobhunt.normalize import annual_in, normalize_location
from jobhunt.sources.base import finalize
from jobhunt.sources.greenhouse import location_ok
from jobhunt.sources.workable import keep_location

US = Geo(home_cc="US", tier1=("Austin",), tier2=("Seattle",), ok_scopes=frozenset({"US", "WORLDWIDE"}))
DE = Geo(home_cc="DE", tier1=("Berlin",), ok_scopes=frozenset(default_ok_scopes("DE")))


@pytest.fixture
def us():
    geo.configure(US)


@pytest.fixture
def de():
    geo.configure(DE)


def test_profile_drives_geo_by_default():
    geo.configure(None)                     # example profile: India, tier1 Bengaluru, tier2 Hyderabad/Pune
    assert normalize_location("Bengaluru, Karnataka")["location_tier"] == 1
    assert normalize_location("Pune")["location_tier"] == 2
    assert normalize_location("Chennai")["location_tier"] == 3
    assert normalize_location("Berlin, Germany")["location_tier"] == 4


def test_default_scopes():
    assert default_ok_scopes("IN") == ["IN", "APAC", "ASIA", "WORLDWIDE"]
    assert set(default_ok_scopes("DE")) >= {"DE", "EMEA", "EU", "WORLDWIDE"}
    assert default_ok_scopes("GB")[0] == "UK"


@pytest.mark.parametrize("loc,tier", [
    ("Austin, TX", 1), ("Seattle, WA", 2), ("New York, NY", 3), ("Denver, CO", 3), ("Remote - US only", 3),
    ("Bengaluru, India", 4), ("London, UK", 4), (None, None),
])
def test_us_tiers(us, loc, tier):
    assert normalize_location(loc)["location_tier"] == tier


def test_us_filters(us):
    assert is_home("Chicago, IL") and is_home("Remote", "US") and is_home("United States")
    assert not is_home("Hyderabad")
    assert location_ok("Remote - US") and location_ok("San Francisco, CA")
    assert not location_ok("Remote, EMEA") and not location_ok("Pune, India")
    assert keep_location("Remote", "US", is_remote=True) and not keep_location("Remote", "DE", is_remote=True)
    assert not foreign_hint("Austin, TX") and foreign_hint("Toronto, Canada")


@pytest.mark.parametrize("loc,tier", [
    ("Berlin", 1), ("Munich, Germany", 3), ("Hamburg", 3), ("Remote - Deutschland", 3), ("Paris, France", 4),
    ("Remote (EU)", 4),
])
def test_de_tiers(de, loc, tier):
    assert normalize_location(loc)["location_tier"] == tier


def test_de_filters(de):
    assert location_ok("Remote (EU)") and location_ok("Remote, EMEA") and location_ok("Berlin")
    assert not location_ok("Remote - US") and not location_ok("Bengaluru, India")
    assert remote_scope("Remote within Europe") == ["EU"]


def _job(title, location, jd="", **kw):
    return finalize(Job(source="t", source_job_id=title + location, title=title, company="X", location=location,
                        jd_text=jd, apply_url="https://jobs.lever.co/x/1", **kw), jd_is_html=False)


def test_gates_follow_profile_languages_and_currency(de):
    prefs = {"experience": {"max_required_years": 5, "skip_title_words": ["director"], "cap_title_words": ["senior"]},
             "location": {"abroad": "skip"}, "languages": ["English", "German"],
             "salary": {"currency": "EUR", "min_per_year": 60000, "below_min_action": "review"}}
    assert not run_gates(_job("Frontend Developer", "Berlin", "Fluent German is required"), prefs).skip
    assert run_gates(_job("Frontend Developer", "Berlin", "Fluent French is required"), prefs).skip
    g = run_gates(_job("Frontend Developer", "Berlin", "", salary_min=40000, salary_max=45000,
                       salary_currency="USD", salary_period="year"), prefs)
    assert "salary_below_floor" in g.flags and g.band_cap == "review"
    assert run_gates(_job("Frontend Developer", "Bengaluru, India", "Relocation support offered"), prefs).skip
    assert run_gates(_job("Director of Engineering", "Berlin"), prefs).skip


def test_annual_in_converts_between_currencies():
    assert annual_in(100_000, "USD", "year", "USD") == 100_000
    assert 7_000_000 < annual_in(100_000, "USD", "year", "INR") < 10_000_000
    assert annual_in(5000, "EUR", "month", "EUR") == 60_000
    assert annual_in(1, "XYZ", "year", "USD") is None


def test_abroad_policy(de):
    base = {"experience": {"max_required_years": 5}, "languages": ["English"], "salary": {}}
    remote_abroad = _job("Frontend Developer", "Remote - Worldwide (US based)", "Work from anywhere worldwide",
                         remote_type="remote")
    assert remote_abroad.location_tier == 4
    assert not run_gates(remote_abroad, {**base, "location": {"abroad": "remote_only"}}).skip
    assert run_gates(remote_abroad, {**base, "location": {"abroad": "skip"}}).skip
