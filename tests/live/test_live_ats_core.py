"""Live smoke for the ATS-core sources: one tiny board per source. Run with: pytest -m live tests/live"""
import pytest

from jobhunt.models import Channel
from jobhunt.sources import ashby, greenhouse, lever, smartrecruiters  # noqa: F401 - registers sources
from jobhunt.sources.base import REGISTRY, SearchContext

pytestmark = pytest.mark.live

BOARDS = {
    "greenhouse": "druva",
    "lever": "zeta",
    "ashby": "sarvam",
    "smartrecruiters": "ServiceNow",
}


@pytest.mark.parametrize("name", list(BOARDS))
def test_live_source_smoke(name):
    ctx = SearchContext(boards={name: [BOARDS[name]]}, limit=2, budgets={name: 6})
    res = REGISTRY[name](ctx)
    assert res.calls >= 1
    assert res.jobs or res.errors, "neither jobs nor a recorded error"
    for j in res.jobs:
        assert j.source == name and j.title and j.apply_url
        assert j.channel == Channel.AUTO and j.ats_type == name
    print(name, len(res.jobs), "jobs", res.calls, "calls", res.errors)
