"""Normalized data models shared by every module."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Channel(StrEnum):
    AUTO = "auto"                                  # employer ATS with a public apply form (you apply there)
    AUTO_PROBE = "auto_probe"                      # employer careers site, probably a public form
    MANUAL_LINKEDIN_NAUKRI = "manual_linkedin_naukri"  # found on LinkedIn/Naukri: search/score/save only
    MANUAL_ACCOUNT_PORTAL = "manual_account_portal"    # portal requires creating an account
    MANUAL_EMAIL = "manual_email"                  # "send your CV to ..."
    MANUAL_OTHER = "manual_other"                  # job boards / unknown


class RemoteType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def company_key(name: str | None) -> str:
    n = _norm(name)
    n = re.sub(r"\b(pvt|private|ltd|limited|inc|llc|llp|corp|corporation|co|technologies|technology|"
               r"solutions|software|labs|india|the)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip() or _norm(name)


def title_key(title: str | None) -> str:
    t = _norm(title)
    t = re.sub(r"\b(sr|senior|jr|junior|i|ii|iii|iv|l\d|level \d)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Job(BaseModel):
    source: str                          # e.g. greenhouse, lever, jobspy_indeed, himalayas
    source_job_id: str
    title: str
    company: str
    source_url: str | None = None        # where we found it
    apply_url: str | None = None         # where to apply (ATS page preferred)
    ats_type: str | None = None          # greenhouse/lever/ashby/workday/... or None
    ats_slug: str | None = None
    location: str | None = None
    country: str | None = None
    remote_type: RemoteType = RemoteType.UNKNOWN
    remote_scope: list[str] = Field(default_factory=list)   # e.g. ["IN"], ["US"], ["EMEA"], ["WORLDWIDE"]
    location_tier: int | None = None     # 1/2 your tier1/tier2 cities, 3 rest of home country, 4 abroad, None unknown
    posted_at: datetime | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None     # year/month/hour
    exp_min: float | None = None
    exp_max: float | None = None
    jd_text: str = ""
    department: str | None = None
    employment_type: str | None = None
    channel: Channel = Channel.MANUAL_OTHER
    raw: dict = Field(default_factory=dict, exclude=True)

    @property
    def job_id(self) -> str:
        h = hashlib.sha1(f"{self.source}|{self.source_job_id}".encode()).hexdigest()
        return h[:12]

    @property
    def dedupe_key(self) -> str:
        loc = _norm(self.location).split(" ")[0] if self.location else ""
        return f"{company_key(self.company)}|{title_key(self.title)}|{loc}"

    @property
    def jd_hash(self) -> str:
        return hashlib.sha1(_norm(self.jd_text).encode()).hexdigest()[:16]
