"""Cheap heuristic pre-score H (0-100). Decides which jobs the agent judges; never the final fit score."""
from __future__ import annotations

from ..models import Job
from ..resume import Resume
from .skills import extract_skills, title_similarity


def heuristic(job: Job, master: Resume, prefs: dict) -> dict:
    text = f"{job.title}\n{job.jd_text}"
    sk = extract_skills(text, master)
    have, gaps = sk["have"], sk["gaps"]
    core = {c.lower() for c in prefs.get("core_skills") or []}      # your positioning: weighted double
    weighted_have = sum(2 if s.lower() in core else 1 for s in have)
    denom = weighted_have + len(gaps)
    skill_pts = 45 * (weighted_have / denom) if denom else 15.0
    skill_pts = min(45.0, skill_pts + min(10, len(have)) * 0.5)   # breadth bonus

    tsim = title_similarity(job.title, prefs.get("target_titles", []))
    title_pts = 25 * max(0.0, (tsim - 40) / 60)

    lo, hi = job.exp_min, job.exp_max
    mine = float((prefs.get("candidate") or {}).get("years_experience") or 0)
    if lo is None:
        exp_pts = 10.0
    elif lo <= mine + 0.25 and (hi is None or hi >= mine - 1):
        exp_pts = 15.0          # you meet the minimum
    elif lo <= mine + 1.5:
        exp_pts = 12.0 if lo <= mine + 0.75 else 8.0
    else:
        exp_pts = 0.0

    tier = job.location_tier
    loc_pts = {1: 10.0, 2: 9.0, 3: 7.0, 4: 4.0}.get(tier, 5.0)
    if job.remote_type.value == "remote" and tier in (3, 4):
        loc_pts += 1.0

    jd_pts = 5.0 if len(job.jd_text or "") > 300 else 0.0   # we can judge it
    h = round(min(100.0, skill_pts + title_pts + exp_pts + loc_pts + jd_pts), 1)
    return {"h": h, "skills_have": have, "skills_gaps": gaps, "title_similarity": round(tsim, 1),
            "parts": {"skills": round(skill_pts, 1), "title": round(title_pts, 1), "experience": exp_pts,
                      "location": loc_pts, "jd": jd_pts}}
