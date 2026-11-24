"""Live smoke for discovery: one tiny call per probe type (run with -m live)."""
import pytest

from jobhunt.sources import discover as D

pytestmark = pytest.mark.live


@pytest.mark.parametrize("ats,slug", [
    ("greenhouse", "observeai"),
    ("lever", "paytm"),
    ("ashby", "sarvam"),
    ("smartrecruiters", "BoschGroup"),
    ("workable", "huggingface"),
])
def test_probe_live_board(ats, slug):
    r = D.probe_board(ats, slug)
    assert r["live"] is True or r["error"], r       # >= 1 job or a recorded, non-crashing error
    if r["live"]:
        assert r["n_jobs"] is not None and r["n_jobs"] >= 1


def test_probe_bad_slug_is_not_live():
    r = D.probe_board("greenhouse", "this-board-does-not-exist-jobhunt")
    assert r["live"] is False


def test_career_page_live():
    errs: list[str] = []
    found = D.career_page_ats("https://paytm.com/careers", errors=errs)
    assert ("lever", "paytm") in found or errs
