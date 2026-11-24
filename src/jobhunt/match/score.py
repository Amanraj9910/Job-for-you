"""Judgment schema (what the coding agent returns) and deterministic score arithmetic."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..resume import Resume

RUBRIC_VERSION = "v1"
WEIGHTS = {"must": 40, "nice": 10, "years": 15, "seniority": 15, "domain": 10, "location": 5, "salary": 5}
STATUS_VALUE = {"met": 1.0, "partial": 0.5, "unknown": 0.5, "unmet": 0.0}


class ReqJudgment(BaseModel):
    req_id: str
    kind: Literal["must", "nice", "resp", "other"]
    jd_quote: str = ""
    status: Literal["met", "partial", "unmet", "unknown"]
    evidence: list[str] = Field(default_factory=list)
    note: str = ""


class Years(BaseModel):
    required: float | None = None
    candidate: float


class Judgment(BaseModel):
    rubric_version: str = RUBRIC_VERSION
    job_id: str
    requirements: list[ReqJudgment]
    years: Years
    seniority_match: Literal["exact", "adjacent", "mismatch"]
    domain_fit: float = Field(ge=0, le=1)
    dealbreakers: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    summary: str = ""
    top_matches: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def judgment_json_schema() -> dict:
    return Judgment.model_json_schema()


def check_evidence(j: Judgment, master: Resume) -> list[str]:
    """Downgrade met/partial without valid evidence. Returns notes about downgrades (mutates j)."""
    # bullets, plus entries (EDU_... for degree requirements, CERT_... for certifications)
    bullet_ids = {b.id for b in master.bullets} | {e.id for e in master.entries}
    skill_level = {s.name.lower(): s.level for s in master.skills}
    notes = []
    for r in j.requirements:
        if r.status not in ("met", "partial"):
            continue
        valid, exposure_only = [], True
        for ev in r.evidence:
            if ev in bullet_ids:
                valid.append(ev)
                exposure_only = False
            elif ev.startswith("SKILL:") and ev[6:].strip().lower() in skill_level:
                valid.append(ev)
                if skill_level[ev[6:].strip().lower()] != "exposure":
                    exposure_only = False
        if not valid:
            notes.append(f"{r.req_id}: {r.status} without valid evidence -> unmet")
            r.status = "unmet"
        elif r.status == "met" and exposure_only:
            notes.append(f"{r.req_id}: only exposure-level evidence -> partial")
            r.status = "partial"
        r.evidence = valid
    return notes


def _ratio(reqs: list[ReqJudgment]) -> float | None:
    if not reqs:
        return None
    return sum(STATUS_VALUE[r.status] for r in reqs) / len(reqs)


def compute_score(j: Judgment, *, location_tier: int | None, remote: bool, salary_ok: bool | None,
                  gate_cap: int | None = None, gate_band_cap: str | None = None, gate_skip: bool = False) -> dict:
    musts = [r for r in j.requirements if r.kind == "must"]
    nices = [r for r in j.requirements if r.kind == "nice"]
    must_r = _ratio(musts)
    if must_r is None:   # no explicit musts: use responsibilities + others as proxy
        must_r = _ratio([r for r in j.requirements if r.kind in ("resp", "other")]) or 0.5
    nice_r = _ratio(nices)
    if nice_r is None:
        nice_r = must_r

    req, cand = j.years.required, j.years.candidate
    if req is None:
        years_r = 0.8
    elif cand >= req:
        years_r = 0.7 if cand - req > 6 else 1.0
    else:
        years_r = max(0.0, 1 - (req - cand) / 3)

    sen_r = {"exact": 1.0, "adjacent": 0.6, "mismatch": 0.0}[j.seniority_match]
    loc_r = {1: 1.0, 2: 1.0, 3: 0.9, 4: 0.8 if remote else 0.6}.get(location_tier, 0.6)
    sal_r = 0.6 if salary_ok is None else (1.0 if salary_ok else 0.2)

    parts = {
        "must": WEIGHTS["must"] * must_r, "nice": WEIGHTS["nice"] * nice_r, "years": WEIGHTS["years"] * years_r,
        "seniority": WEIGHTS["seniority"] * sen_r, "domain": WEIGHTS["domain"] * j.domain_fit,
        "location": WEIGHTS["location"] * loc_r, "salary": WEIGHTS["salary"] * sal_r,
    }
    raw = sum(parts.values())
    caps = []
    must_ids = {r.req_id for r in musts}
    unmet_ids = {r.req_id for r in j.requirements if r.status == "unmet"}
    if set(j.dealbreakers) & must_ids & unmet_ids:
        caps.append(("missing dealbreaker", 70))
    if j.seniority_match == "mismatch":
        caps.append(("seniority mismatch", 64))
    if gate_cap is not None:
        caps.append(("gate", gate_cap))
    score = raw
    for _, c in caps:
        score = min(score, c)
    score = round(score, 1)

    if gate_skip:
        band = "skip"
    elif score >= 80:
        band = "apply" if j.confidence != "low" else "review"
    elif score >= 65:
        band = "maybe"
    else:
        band = "skip"
    order = ["apply", "review", "maybe", "skip"]
    if gate_band_cap and order.index(band) < order.index(gate_band_cap):
        band = gate_band_cap
    return {"fit_score": score, "raw_score": round(raw, 1), "band": band, "confidence": j.confidence,
            "parts": {k: round(v, 1) for k, v in parts.items()}, "caps": caps, "rubric_version": RUBRIC_VERSION}
