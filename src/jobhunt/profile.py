"""Your profile: ``profile/preferences.yaml`` (what you want) and ``profile/resume.yaml`` (facts about you).

Both files are validated with pydantic so typos and missing fields are reported with a clear message
(``jobhunt profile validate``). Everything personal that the search, scoring, vetting and report use comes from
here — nothing about a specific person is hard-coded in the code.

Start from ``profile.example/`` (``jobhunt init`` copies it), the browser form (``jobhunt profile form``), or let
your coding agent draft both files from your resume (``playbooks/setup.md``).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import settings

DEFAULT_SKIP_TITLE_WORDS = ["staff", "principal", "director", "head", "vp", "vice president", "chief", "cto",
                            "manager", "distinguished", "fellow", "architect"]
DEFAULT_CAP_TITLE_WORDS = ["senior", "sr", "lead"]
DEFAULT_ROLE_WORDS = ["engineer", "engineering", "developer", "scientist", "architect", "programmer", "sde", "swe",
                      "consultant", "specialist", "analyst", "designer", "researcher", "associate"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Candidate(_Model):
    years_experience: float = Field(0, ge=0, le=60, description="Full-time professional experience in years")
    work_authorization: str = Field("", description="e.g. 'Citizen of India; needs visa sponsorship elsewhere'")
    notice_period: str = ""
    summary: str = Field("", description="One line about you; shown to the agent when it judges fit")


class TitleKeywords(_Model):
    strong: list[str] = Field(default_factory=list, description="A title with one of these is clearly relevant")
    related: list[str] = Field(default_factory=list, description="Relevant only next to a role word (engineer…)")
    exclude: list[str] = Field(default_factory=list, description="Never show titles containing these")
    role_words: list[str] = Field(default_factory=lambda: list(DEFAULT_ROLE_WORDS))


class Experience(_Model):
    max_required_years: float = Field(3, ge=0, le=60)
    skip_title_words: list[str] = Field(default_factory=lambda: list(DEFAULT_SKIP_TITLE_WORDS))
    cap_title_words: list[str] = Field(default_factory=lambda: list(DEFAULT_CAP_TITLE_WORDS))


class Location(_Model):
    home_country: str = Field("IN", min_length=2, max_length=2, description="ISO-3166 alpha-2, e.g. IN, US, DE, GB")
    tier1: list[str] = Field(default_factory=list, description="First-choice cities")
    tier2: list[str] = Field(default_factory=list, description="Second-choice cities")
    extra_cities: list[str] = Field(default_factory=list, description="Home-country cities the built-in list lacks")
    remote_ok_scopes: list[str] | None = Field(None, description="Remote scopes that include you; None = default")
    abroad: Literal["skip", "remote_only", "relocation"] = "relocation"

    @field_validator("home_country")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.strip().upper()
        return "GB" if v == "UK" else v


class Salary(_Model):
    currency: str = Field("USD", min_length=3, max_length=3)
    min_per_year: float | None = Field(None, ge=0, description="Jobs stating less than this are flagged")
    below_min_action: Literal["review", "skip"] = "review"

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.strip().upper()


class Search(_Model):
    queries: dict[int, list[str]] = Field(..., description="tier -> search phrases; tiers 1-2 run every time")
    locations: list[str] = Field(default_factory=list, description="Where Indeed/Glassdoor/LinkedIn search")
    remote_locations: list[str] = Field(default_factory=lambda: ["Remote"])
    sources: list[str] | None = Field(None, description="None = all default sources")
    disabled_sources: list[str] = Field(default_factory=list)
    budgets: dict[str, int | list[str]] = Field(default_factory=dict, description="max calls per run, per source "
                                                "(wwr_feeds / yc_jobs_paths take lists)")
    posted_within_days: int = Field(7, ge=1, le=60)
    results_per_call: int = Field(30, ge=1, le=100)
    max_boards: int = Field(300, ge=0, le=5000, description="company ATS boards polled per run")


class Exclusions(_Model):
    companies: list[str] = Field(default_factory=list)


class Vetting(_Model):
    reject_rating_below: float = 2.5
    caution_rating_below: float = 3.0
    min_reviews: int = 50
    cache_days: int = 90
    review_sites: list[str] = Field(default_factory=lambda: ["Glassdoor", "Indeed"])
    forums: list[str] = Field(default_factory=lambda: ["reddit"])
    extra_queries: list[str] = Field(default_factory=list, description="Extra searches; {company} is replaced")


class Report(_Model):
    title: str = "My job matches"
    posted_within_days: int = Field(30, ge=1, le=365)


class Preferences(_Model):
    kind: Literal["preferences"] = "preferences"
    schema_version: int = 1
    candidate: Candidate = Field(default_factory=Candidate)
    target_titles: list[str] = Field(..., min_length=1)
    title_keywords: TitleKeywords = Field(default_factory=TitleKeywords)
    core_skills: list[str] = Field(default_factory=list, description="Weighted double when a JD mentions them")
    languages: list[str] = Field(default_factory=lambda: ["English"])
    experience: Experience = Field(default_factory=Experience)
    location: Location = Field(default_factory=Location)
    salary: Salary = Field(default_factory=Salary)
    search: Search
    exclusions: Exclusions = Field(default_factory=Exclusions)
    vetting: Vetting = Field(default_factory=Vetting)
    report: Report = Field(default_factory=Report)


# ------------------------------------------------------------------------------------------ loading
class ProfileError(RuntimeError):
    """The profile is missing or invalid; the message says what to do."""


def profile_dir() -> Path:
    return settings().profile_dir


def _read_yaml(name: str) -> dict:
    p = profile_dir() / name
    if not p.exists():
        raise ProfileError(f"{p} not found. Run `jobhunt init` (copies profile.example/) or `jobhunt profile form`, "
                           f"or ask your coding agent to follow playbooks/setup.md.")
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"{p}: not valid YAML: {e}") from e


def _fmt(e: ValidationError, file: str) -> str:
    lines = [f"{file}:"]
    for err in e.errors():
        where = ".".join(str(x) for x in err["loc"]) or "(top level)"
        lines.append(f"  - {where}: {err['msg']}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def preferences_model() -> Preferences:
    try:
        return Preferences.model_validate(_read_yaml("preferences.yaml"))
    except ValidationError as e:
        raise ProfileError(_fmt(e, "preferences.yaml")) from e


@lru_cache(maxsize=1)
def preferences() -> dict:
    """preferences.yaml as a plain dict with every default filled in."""
    return preferences_model().model_dump()


def title_keywords() -> dict:
    tk = dict(preferences()["title_keywords"])
    tk["include"] = [*tk.get("strong", []), *tk.get("related", [])]
    return tk


def search_cfg() -> dict:
    return preferences()["search"]


def reload() -> None:
    """Forget cached profile files (after editing them in the same process)."""
    from . import geo
    preferences_model.cache_clear()
    preferences.cache_clear()
    from .resume import load_resume
    load_resume.cache_clear()
    geo.configure(None)


def validate_profile() -> list[str]:
    """Human-readable problems with preferences.yaml / resume.yaml ([] = all good)."""
    errors: list[str] = []
    try:
        reload()
        prefs = preferences_model()
    except ProfileError as e:
        errors.append(str(e))
        prefs = None
    try:
        from .resume import load_resume, validate_resume
        r = load_resume()
        errors += validate_resume(r)
    except ProfileError as e:
        errors.append(str(e))
        r = None
    if prefs and r:
        known = {s.name.lower() for s in r.skills}
        missing = [c for c in prefs.core_skills if c.lower() not in known]
        if missing:
            errors.append(f"preferences.yaml: core_skills not in resume.yaml skills: {', '.join(missing)}")
        if not prefs.search.queries.get(1) and not prefs.search.queries.get(2):
            errors.append("preferences.yaml: search.queries needs at least tier 1 or 2")
        if not (prefs.title_keywords.strong or prefs.title_keywords.related):
            errors.append("preferences.yaml: title_keywords.strong (or related) is empty — company boards would "
                          "return every job")
    return errors


def profile_notes() -> list[str]:
    """Non-fatal hints about the profile (shown by `profile validate` and `doctor`)."""
    from .geo import COUNTRIES
    notes = []
    try:
        cc = preferences_model().location.home_country
    except ProfileError:
        return notes
    if cc not in COUNTRIES:
        notes.append(f"location.home_country {cc} is not in the built-in country list (geo.py): city detection is "
                     f"limited to your tier1/tier2/extra_cities — consider contributing the country")
    return notes


# kept for older imports
def queries() -> dict:
    s = search_cfg()
    return {"tiers": s["queries"], "budgets": s["budgets"], "title_keywords": title_keywords(),
            "locations": {"home": s["locations"], "remote_global": s["remote_locations"]}}
