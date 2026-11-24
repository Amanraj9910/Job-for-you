"""Hard gates (code, no LLM): decide skip / cap / flags before any judging.

Every threshold comes from your profile (preferences.yaml): seniority words, max years required, home country
and remote scopes, languages you speak, salary floor and currency, excluded companies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..models import Job, RemoteType
from ..normalize import annual_in, ok_scopes, remote_scope

_ASSOC_ARCH = re.compile(r"\bassociate\s+(solutions?\s+)?architect\b", re.I)
_NO_SPONSOR = re.compile(r"(no|not|unable to|cannot|can't|do not|does not)\s+(offer\s+|provide\s+)?"
                         r"(visa\s+)?sponsor|must (already )?(be|have) (legally )?(authori[sz]ed|eligible) to work|"
                         r"without (the need for )?(visa )?sponsorship|sponsorship is not available", re.I)
_RELOCATION = re.compile(r"relocation (support|assistance|package|bonus)|we (will )?(help|support) (you )?relocat|"
                         r"visa sponsorship (is )?(available|provided|offered)|we sponsor visas|will sponsor", re.I)
LANGUAGES = ["german", "french", "japanese", "mandarin", "chinese", "spanish", "dutch", "korean", "portuguese",
             "italian", "arabic", "polish", "swedish", "english", "hindi", "russian", "turkish", "danish",
             "norwegian", "finnish", "czech", "hungarian", "romanian", "greek", "hebrew", "thai", "vietnamese",
             "indonesian", "malay", "cantonese"]


@lru_cache(maxsize=16)
def _lang_rx(known: tuple[str, ...]) -> re.Pattern | None:
    others = [lang for lang in LANGUAGES if lang not in known]
    if not others:
        return None
    alt = "|".join(others)
    return re.compile(rf"\b(fluent|fluency|native|proficien\w*|business[- ]level)\s+(in\s+)?({alt})\b|"
                      rf"\b({alt})\s+(is\s+)?(required|mandatory|a must)\b", re.I)


@lru_cache(maxsize=16)
def _words_rx(words: tuple[str, ...]) -> re.Pattern | None:
    words = tuple(w.strip() for w in words if w and w.strip())
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\.?(?!\w)", re.I)


@dataclass
class GateResult:
    skip: bool = False
    cap: int | None = None            # max fit score allowed
    band_cap: str | None = None       # e.g. 'maybe' / 'review'
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def add(self, reason: str, *, skip: bool = False, cap: int | None = None, band_cap: str | None = None,
            flag: str | None = None) -> None:
        self.reasons.append(reason)
        self.skip = self.skip or skip
        if cap is not None:
            self.cap = cap if self.cap is None else min(self.cap, cap)
        if band_cap:
            order = ["apply", "review", "maybe", "skip"]
            if self.band_cap is None or order.index(band_cap) > order.index(self.band_cap):
                self.band_cap = band_cap
        if flag:
            self.flags.append(flag)

    def as_dict(self) -> dict:
        return {"skip": self.skip, "cap": self.cap, "band_cap": self.band_cap, "reasons": self.reasons,
                "flags": self.flags}


def salary_ok(job: Job, prefs: dict) -> bool | None:
    """True/False against your floor (in your currency); None when the job states no usable salary."""
    sal = prefs.get("salary") or {}
    floor, cur = sal.get("min_per_year"), sal.get("currency") or "USD"
    if not floor:
        return None
    hi = annual_in(job.salary_max or job.salary_min, job.salary_currency, job.salary_period, cur, default_currency=cur)
    return None if hi is None else hi >= floor


def run_gates(job: Job, prefs: dict, *, excluded_companies: set[str] | None = None,
              already_applied: bool = False) -> GateResult:
    g = GateResult()
    title = job.title or ""
    jd = job.jd_text or ""
    exp_cfg = prefs.get("experience", {})
    max_req = exp_cfg.get("max_required_years", 3)
    skip_rx = _words_rx(tuple(exp_cfg.get("skip_title_words") or ()))
    cap_rx = _words_rx(tuple(exp_cfg.get("cap_title_words") or ()))
    upper = min(9, int(max_req) - 1)      # "senior" is fine when the JD asks for clearly fewer years than your max
    small_req = re.compile(rf"\b[1-{upper}]\s*\+?\s*(years?|yrs?)\b", re.I) if upper >= 1 else None

    # --- title seniority
    if skip_rx and skip_rx.search(title) and not _ASSOC_ARCH.search(title):
        g.add(f"title too senior: {title}", skip=True)
    elif cap_rx and cap_rx.search(title):
        if not ((job.exp_min is not None and job.exp_min <= max_req) or (small_req and small_req.search(jd[:4000]))):
            g.add(f"senior/lead title without a <={max_req:g} yr requirement", band_cap="maybe")

    # --- experience
    if job.exp_min is not None and job.exp_min > max_req:
        g.add(f"requires {job.exp_min:g}+ years (max {max_req:g})", skip=True)

    # --- location eligibility
    abroad = (prefs.get("location") or {}).get("abroad", "relocation")
    tier = job.location_tier
    scope = set(job.remote_scope or [])
    if job.remote_type == RemoteType.REMOTE and not scope:
        scope = set(remote_scope(jd[:3000]))   # "work from anywhere", "US-based only" in the JD body
    if tier == 4:
        if job.remote_type == RemoteType.REMOTE and abroad == "skip":
            g.add("remote job based abroad (location.abroad = skip)", skip=True)
        elif job.remote_type == RemoteType.REMOTE:
            if scope and not scope & ok_scopes():
                g.add(f"remote restricted to {sorted(scope)}", skip=True)
            elif not scope:
                g.add("remote, region unknown", flag="remote_scope_unknown", band_cap="review")
        elif abroad == "relocation" and _RELOCATION.search(jd):
            g.add("abroad onsite with relocation/sponsorship offered", flag="abroad_relocation")
        else:
            g.add("onsite/hybrid abroad" + (" without relocation support" if abroad == "relocation" else ""),
                  skip=True)
        if _NO_SPONSOR.search(jd) and job.remote_type != RemoteType.REMOTE:
            g.add("explicitly no visa sponsorship (abroad)", skip=True)
        elif _NO_SPONSOR.search(jd):
            g.add("mentions work authorisation requirement", cap=40, flag="work_auth")
    elif tier is None and job.remote_type != RemoteType.REMOTE:
        g.add("location unknown", flag="location_unknown")

    # --- language
    known = tuple(sorted(lang.strip().lower() for lang in prefs.get("languages") or ["English"]))
    lrx = _lang_rx(known)
    if lrx and lrx.search(jd):
        g.add("requires a language you did not list", skip=True)

    # --- salary
    ok = salary_ok(job, prefs)
    if ok is False:
        sal = prefs.get("salary") or {}
        why = f"stated salary below {sal.get('min_per_year'):,.0f} {sal.get('currency')}/year"
        if sal.get("below_min_action") == "skip":
            g.add(why, skip=True, flag="salary_below_floor")
        else:
            g.add(why, band_cap="review", flag="salary_below_floor")

    # --- exclusions
    if excluded_companies and (job.company or "").strip().lower() in excluded_companies:
        g.add("company excluded in your profile", skip=True)
    if already_applied:
        g.add("already applied", skip=True)
    return g
