"""Live smoke tests: one tiny call per source. Run with: pytest -m live tests/live/test_live_jobspy_serp.py"""
import pytest

from jobhunt.config import env
from jobhunt.sources import jobspy_src as J
from jobhunt.sources import serpapi as S
from jobhunt.sources.base import SearchContext

pytestmark = pytest.mark.live


def _ctx(name: str) -> SearchContext:
    return SearchContext(queries=["python developer"], locations=["Bengaluru"], hours_old=168, limit=5,
                         budgets={name: 1})


@pytest.mark.parametrize("site", ["indeed", "glassdoor", "naukri", "linkedin"])
def test_live_jobspy(site):
    r = J.run_site(site, _ctx(f"jobspy_{site}"))
    assert r.calls == 1
    assert r.jobs or r.errors, "no jobs and no recorded error"
    for j in r.jobs:
        assert j.source == f"jobspy_{site}" and j.title and j.apply_url
        if site in ("linkedin", "naukri") and j.apply_url == j.source_url:
            assert j.channel.value == "manual_linkedin_naukri"


@pytest.mark.skipif(not env("SERPAPI_KEY"), reason="SERPAPI_KEY not set")
def test_live_serpapi():
    r = S.search(_ctx("serpapi"))
    assert r.calls == 1
    assert r.jobs or r.errors
