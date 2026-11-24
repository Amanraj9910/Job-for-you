"""Gates, heuristic, packet, evidence check, score arithmetic and bands (example profile: Sam Example, IN)."""
import pytest

from jobhunt.match.gates import run_gates
from jobhunt.match.heuristic import heuristic
from jobhunt.match.packet import build_packet, split_requirements
from jobhunt.match.score import Judgment, check_evidence, compute_score
from jobhunt.match.skills import extract_skills, title_similarity
from jobhunt.models import Job, RemoteType
from jobhunt.profile import preferences
from jobhunt.resume import load_resume
from jobhunt.sources.base import finalize

PREFS = None


@pytest.fixture(autouse=True)
def _prefs():
    global PREFS
    PREFS = preferences()


@pytest.fixture(scope="module")
def master():
    return load_resume()


def mk(title="AI Engineer", location="Bengaluru, India", jd="", **kw) -> Job:
    j = Job(source="test", source_job_id=title + (location or ""), title=title, company=kw.pop("company", "Acme"),
            location=location, jd_text=jd, apply_url=kw.pop("apply_url", "https://jobs.lever.co/acme/1"), **kw)
    return finalize(j, jd_is_html=False)


# ---------------------------------------------------------------- gates
GATE_CASES = [
    # title, location, jd, extra, expect_skip, expect_band_cap
    ("AI Engineer", "Bengaluru", "2+ years Python", {}, False, None),
    ("Staff AI Engineer", "Bengaluru", "", {}, True, None),
    ("Principal ML Engineer", "Remote - India", "", {}, True, None),
    ("Engineering Manager, AI", "Pune", "", {}, True, None),
    ("Head of AI", "Bengaluru", "", {}, True, None),
    ("Solutions Architect", "Bengaluru", "", {}, True, None),
    ("Associate Solutions Architect", "Bengaluru", "", {}, False, None),
    ("Senior AI Engineer", "Bengaluru", "5+ years of experience", {}, True, None),
    ("Senior AI Engineer", "Bengaluru", "We want 2+ years of experience with LLMs", {}, False, None),
    ("Senior AI Engineer", "Bengaluru", "Great culture", {}, False, "maybe"),
    ("AI Engineer", "Bengaluru", "Minimum 5 years of experience", {}, True, None),
    ("AI Engineer", "Remote - US only", "", {"remote_type": RemoteType.REMOTE}, True, None),
    ("AI Engineer", "Remote", "Work from anywhere worldwide", {}, False, None),
    ("AI Engineer", "Remote", "Remote role", {}, False, "review"),
    ("AI Engineer", "Berlin, Germany", "Onsite in Berlin", {}, True, None),
    ("AI Engineer", "Berlin, Germany", "We offer relocation support and visa sponsorship is available", {}, False, None),
    ("AI Engineer", "London, UK", "Candidates must be authorized to work in the UK. No visa sponsorship.", {}, True, None),
    ("AI Engineer", "Bengaluru", "Fluent German is required", {}, True, None),
    ("AI Engineer", "Bengaluru", "CTC 6-8 LPA", {"salary_min": 600000, "salary_max": 800000,
                                                  "salary_currency": "INR", "salary_period": "year"}, False, "review"),
    ("AI Engineer", "Pune", "CTC 12-15 LPA", {"salary_min": 1200000, "salary_max": 1500000,
                                                 "salary_currency": "INR", "salary_period": "year"}, False, None),
    ("Lead GenAI Developer", "Hyderabad", "1-3 years experience", {}, False, None),
]


@pytest.mark.parametrize("title,loc,jd,extra,skip,band_cap", GATE_CASES)
def test_gates(title, loc, jd, extra, skip, band_cap):
    g = run_gates(mk(title, loc, jd, **extra), PREFS)
    assert g.skip is skip, g.reasons
    if not skip:
        assert g.band_cap == band_cap, g.reasons


def test_gate_excluded_and_applied():
    j = mk()
    assert run_gates(j, PREFS, excluded_companies={"acme"}).skip
    assert run_gates(j, PREFS, already_applied=True).skip


# ---------------------------------------------------------------- skills / heuristic
def test_extract_skills(master):
    s = extract_skills("Build RAG with the OpenAI API, LangChain and Kubernetes. Python, Go a plus.", master)
    assert {"RAG", "LLM APIs", "Python", "LangChain"} <= set(s["have"])
    assert "Kubernetes" in s["gaps"] and "Go" in s["gaps"] and "LangChain" not in s["gaps"]


def test_short_tokens_exact_only(master):
    s = extract_skills("We go to market quickly; good communication.", master)
    assert "Go" not in s["gaps"]


def test_title_similarity():
    t = PREFS["target_titles"]
    assert title_similarity("Senior Generative AI Engineer (LLM)", t) >= 90
    assert title_similarity("Accountant", t) < 50


def test_heuristic_orders_sensibly(master):
    good = mk("LLM Engineer", "Bengaluru", "1-3 years. Python, FastAPI, RAG, OpenAI API, pgvector, AWS, Docker. " * 5)
    bad = mk("Java Developer", "Bengaluru", "5+ years Java, Spring Boot, Kafka, Kubernetes, AWS. " * 5)
    assert heuristic(good, master, PREFS)["h"] >= 70
    assert heuristic(bad, master, PREFS)["h"] < 45


# ---------------------------------------------------------------- packet
JD = """About us
We are a fast-growing AI startup.

Requirements:
- 2+ years of Python experience
- Hands-on with LLMs, RAG and vector databases
- Experience with Azure or AWS

Nice to have:
- Computer vision experience
- LangGraph

Responsibilities
- Build agentic workflows for customers
"""


def test_split_requirements():
    reqs = split_requirements(JD)
    kinds = [r["kind"] for r in reqs]
    assert kinds == ["must", "must", "must", "nice", "nice", "resp"]
    assert reqs[0]["req_id"] == "R1"


def test_packet(master):
    j = mk(jd=JD)
    g = run_gates(j, PREFS).as_dict()
    p = build_packet(j, "abc", master, PREFS, None, g, heuristic(j, master, PREFS))
    assert p["job"]["job_id"] == "abc" and len(p["requirements"]) == 6 and p["packet_hash"]
    assert any(b["id"] == "EXP_ACME.B1" for b in p["candidate"]["bullets"])
    assert p["candidate"]["experience_years_fulltime"] == 2 and "Bengaluru" in p["candidate"]["location_priority"]


# ---------------------------------------------------------------- score arithmetic
def judgment(statuses, kinds=None, **kw):
    kinds = kinds or ["must"] * len(statuses)
    reqs = [{"req_id": f"R{i+1}", "kind": k, "status": s, "evidence": ["EXP_ACME.B1"] if s in ("met", "partial") else []}
            for i, (s, k) in enumerate(zip(statuses, kinds, strict=True))]
    base = {"job_id": "x", "requirements": reqs, "years": {"required": 2, "candidate": 1.4},
            "seniority_match": "exact", "domain_fit": 0.9, "confidence": "high"}
    base.update(kw)
    return Judgment.model_validate(base)


def test_perfect_fit_is_apply():
    r = compute_score(judgment(["met"] * 4, years={"required": 1, "candidate": 1.4}),
                      location_tier=2, remote=False, salary_ok=True)
    assert r["fit_score"] >= 95 and r["band"] == "apply"


def test_partial_fit_maybe():
    r = compute_score(judgment(["met", "partial", "unmet", "met"]), location_tier=3, remote=False, salary_ok=None)
    assert 60 <= r["fit_score"] < 80


def test_dealbreaker_caps_at_70():
    j = judgment(["met", "met", "met", "unmet"], dealbreakers=["R4"], years={"required": 1, "candidate": 1.4})
    r = compute_score(j, location_tier=1, remote=False, salary_ok=True)
    assert r["fit_score"] <= 70


def test_low_confidence_goes_to_review():
    j = judgment(["met"] * 5, confidence="low", years={"required": 1, "candidate": 1.4})
    assert compute_score(j, location_tier=1, remote=False, salary_ok=True)["band"] == "review"


def test_gate_caps_and_skip():
    j = judgment(["met"] * 5, years={"required": 1, "candidate": 1.4})
    assert compute_score(j, location_tier=1, remote=False, salary_ok=True, gate_cap=40)["fit_score"] == 40
    assert compute_score(j, location_tier=1, remote=False, salary_ok=True, gate_skip=True)["band"] == "skip"
    assert compute_score(j, location_tier=1, remote=False, salary_ok=True, gate_band_cap="maybe")["band"] == "maybe"


def test_seniority_mismatch_caps():
    j = judgment(["met"] * 5, seniority_match="mismatch")
    assert compute_score(j, location_tier=1, remote=False, salary_ok=True)["fit_score"] <= 64


def test_years_gap_penalised():
    near = compute_score(judgment(["met"] * 3, years={"required": 2, "candidate": 1.4}), location_tier=1,
                         remote=False, salary_ok=True)["parts"]["years"]
    far = compute_score(judgment(["met"] * 3, years={"required": 4, "candidate": 1.4}), location_tier=1,
                        remote=False, salary_ok=True)["parts"]["years"]
    assert near > far


def test_evidence_downgrade(master):
    j = judgment(["met", "met", "partial"])
    j.requirements[0].evidence = ["NOPE.B9"]
    j.requirements[1].evidence = ["SKILL:PyTorch"]       # exposure-level skill
    j.requirements[2].evidence = ["SKILL:Python"]
    notes = check_evidence(j, master)
    assert j.requirements[0].status == "unmet"
    assert j.requirements[1].status == "partial"
    assert j.requirements[2].status == "partial" and len(notes) == 2


def test_deterministic():
    j = judgment(["met", "partial", "met"])
    a = compute_score(j, location_tier=2, remote=False, salary_ok=None)
    b = compute_score(j, location_tier=2, remote=False, salary_ok=None)
    assert a == b
