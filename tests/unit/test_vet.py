"""TG5-auto: red-flag scan, verdict schema, deterministic vetting rules."""
import pytest
from pydantic import ValidationError

from jobhunt.vet.redflags import scan, worst
from jobhunt.vet.verdict import Verdict, apply_rules, research_packet

CFG = {"reject_rating_below": 2.5, "caution_rating_below": 3.0, "min_reviews": 50}

JD_CASES = [
    ("Selected candidates must sign a 2-year service bond with the company.", "service_bond", "reject"),
    ("There is a bond of 2 years and Rs 2,00,000.", "service_bond", "reject"),
    ("Training agreement applicable for freshers.", "service_bond", "reject"),
    ("Minimum service period of 18 months is required.", "minimum_service_period", "reject"),
    ("A penalty if you leave before 2 years will be charged.", "leaving_penalty", "reject"),
    ("A refundable security deposit of INR 25,000 is to be paid at joining.", "security_deposit", "reject"),
    ("Registration fee of Rs 1500 is required to process your application.", "candidate_fee", "reject"),
    ("Candidates must submit original certificates at joining.", "original_documents", "reject"),
    ("Send your resume on WhatsApp to 98xxxxxx.", "off_platform_contact", "caution"),
    ("Mail your CV to hr.hiring.team@gmail.com", "free_email_recruiter", "caution"),
    ("Our client is a leading MNC in the IT space.", "undisclosed_client", "caution"),
    ("Earn up to Rs 5000 per day working from home!", "too_good_pay", "caution"),
]


@pytest.mark.parametrize("text,flag,sev", JD_CASES)
def test_redflag_scan(text, flag, sev):
    flags = scan(text)
    assert any(f["flag"] == flag and f["severity"] == sev for f in flags), flags


def test_clean_jd_has_no_flags():
    jd = ("We are hiring an AI Engineer with 2+ years of Python and Azure OpenAI experience. Competitive salary, "
          "health insurance, hybrid work in Bengaluru. Apply via our careers page.")
    assert scan(jd) == [] and worst([]) is None


def test_bond_word_in_finance_context_not_flagged():
    assert not scan("Experience with bond pricing models and fixed income analytics.")


def _v(**kw):
    base = {"company": "Acme", "verdict": "ok",
            "reasons": [{"text": "Well-reviewed product company", "url": "https://www.ambitionbox.com/x", "kind": "legit"}],
            "ratings": [{"site": "ambitionbox", "rating": 4.1, "reviews": 800}]}
    base.update(kw)
    return Verdict.model_validate(base)


def test_ok_company():
    assert apply_rules(_v(), [], CFG)[0] == "ok"


def test_bond_company_rejected():
    assert apply_rules(_v(bond_flag=True), [], CFG)[0] == "reject"


def test_jd_flag_overrides_ok():
    assert apply_rules(_v(), scan("must sign a 1 year service bond"), CFG)[0] == "reject"


def test_low_rating_reject_and_caution():
    assert apply_rules(_v(ratings=[{"site": "glassdoor", "rating": 2.2, "reviews": 120}]), [], CFG)[0] == "reject"
    assert apply_rules(_v(ratings=[{"site": "glassdoor", "rating": 2.8, "reviews": 120}]), [], CFG)[0] == "caution"
    # few reviews: low rating is only a caution
    assert apply_rules(_v(ratings=[{"site": "glassdoor", "rating": 2.0, "reviews": 12}]), [], CFG)[0] == "caution"


def test_agency_cases():
    assert apply_rules(_v(employer_identified=False), scan("Our client is a leading MNC"), CFG)[0] == "caution"
    assert apply_rules(_v(employer_identified=False), scan("Registration fee of Rs 500 applies"), CFG)[0] == "reject"


def test_gmail_impersonation_is_caution_at_least():
    v = _v(posting_on_official_site=False)
    verdict, notes = apply_rules(v, scan("Send CV to acme.careers.hr@gmail.com"), CFG)
    assert verdict == "caution" and any("official" in n for n in notes)


def test_no_footprint_is_caution():
    v = _v(ratings=[], reasons=[{"text": "nothing found", "kind": "footprint"}], verdict="caution")
    assert apply_rules(v, [], CFG)[0] == "caution"


def test_claude_reject_respected():
    assert apply_rules(_v(verdict="reject", reasons=[{"text": "fake job reports", "url": "https://reddit.com/x",
                                                       "kind": "fraud"}]), [], CFG)[0] == "reject"


def test_verdict_needs_source_url():
    with pytest.raises(ValidationError):
        Verdict.model_validate({"company": "A", "verdict": "ok", "reasons": [{"text": "seems fine"}]})


def test_research_packet():
    p = research_packet("Acme Pvt Ltd", "acme.com", "jd", [], "https://jobs.lever.co/acme/1", CFG)
    assert p["company_key"] == "acme" and any("bond" in q for q in p["queries"])
