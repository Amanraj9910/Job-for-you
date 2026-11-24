import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("JOBHUNT_HOME", str(ROOT))
# Tests always run against the shareable example profile (a fictional, India-based persona), never yours.
os.environ["JOBHUNT_PROFILE"] = str(ROOT / "profile.example")


# Source/normalizer tests pin an explicit geography (India; tier 1 Indore, tier 2 Bengaluru) so their expected
# tiers do not change when the example profile is edited. Profile-driven geography is tested in test_geo.py.
def india_test_geo():
    from jobhunt.geo import Geo
    return Geo(home_cc="IN", tier1=("Indore",), tier2=("Bengaluru",),
               ok_scopes=frozenset({"IN", "APAC", "ASIA", "WORLDWIDE"}))


@pytest.fixture(autouse=True)
def _fresh_profile_caches():
    """Profile/geo caches are per-process; reset them around every test so monkeypatches don't leak."""
    from jobhunt import geo, profile
    profile.reload()
    geo.configure(india_test_geo())
    yield
    geo.configure(None)
    profile.reload()


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Isolated DB/output dirs; the profile is still profile.example."""
    from jobhunt import config
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("JOBHUNT_OUTPUT", str(tmp_path / "output"))
    config.settings.cache_clear()
    s = config.settings()
    object.__setattr__(s, "logs_dir", tmp_path / "logs")
    s.ensure_dirs()
    yield s
    config.settings.cache_clear()
