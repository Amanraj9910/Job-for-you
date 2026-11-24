"""Live smoke tests for the remote-board sources (one tiny call each). Run: pytest -m live tests/live

Remotive and RemoteOK share the real on-disk daily cap (data/cache/*_calls.json), so a run near the cap
records a "daily cap" error instead of calling — which still counts as a non-crashing result.
"""
import pytest

from jobhunt.sources import himalayas, hn, remoteok, remotive, wwr, yc_jobs
from jobhunt.sources.base import SearchContext

pytestmark = pytest.mark.live


def _ok(res):
    assert res.jobs or res.errors, f"{res.source}: no jobs and no recorded error"
    for j in res.jobs:
        assert j.title and j.company and j.source == res.source and j.source_job_id
    print(f"\n{res.source}: {len(res.jobs)} jobs, {res.calls} calls, errors={res.errors[:2]}")


def test_live_himalayas():
    _ok(himalayas.search(SearchContext(queries=["python"], limit=5, budgets={"himalayas": 1})))


def test_live_remotive():
    _ok(remotive.search(SearchContext(queries=["python"], limit=5, budgets={"remotive": 1})))


def test_live_remoteok():
    _ok(remoteok.search(SearchContext(title_include=["engineer", "developer"], limit=5)))


def test_live_wwr():
    _ok(wwr.search(SearchContext(limit=5, budgets={"wwr_feeds": ["remote-programming-jobs"]})))


def test_live_yc_jobs():
    _ok(yc_jobs.search(SearchContext(limit=3, budgets={"yc_jobs_paths": ["/jobs/location/india"], "yc_jobs": 3})))


def test_live_hn_thread(tmp_path):
    t = hn.fetch_thread(out_dir=tmp_path)
    assert t.story_id and t.path.exists() and t.total_top_level > 0
    print(f"\nhn: story {t.story_id} '{t.title}': {t.total_top_level} top-level, {len(t.comments)} relevant")
