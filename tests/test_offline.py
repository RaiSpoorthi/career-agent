"""Free parts: profile, keyword matching, fit rules, tracker, gaps."""

import pytest

from career import fit, keywords, taxonomy
from career.profile import Profile
from career.tracker import Tracker

IAM_JD = """IAM Analyst, Bengaluru. 0-2 years of experience. Azure AD / Entra ID, Active Directory, SSO, MFA,
access reviews, provisioning, PIM, ServiceNow, PowerShell. Nice to have: Okta, SailPoint, Splunk SIEM. CTC 8-10 LPA."""
SW_JD = """Junior Software Engineer, Remote (US company). React, TypeScript, Node.js, Express, REST APIs, PostgreSQL,
Docker, CI/CD, unit testing, agile sprints, code reviews. We do not sponsor visas. $85,000. 1+ years of experience."""


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


# ---- profile ----------------------------------------------------------------
def test_profile_loads_and_tracks_are_valid(profile):
    assert set(profile.tracks) == {"security", "software"}
    assert profile.skill_level("SIEM") == "academic"
    assert profile.skill_level("Azure AD") == "work"


def test_profile_rejects_unknown_skill_in_track(tmp_path, profile):
    text = (Profile.load().data, )
    import yaml
    data = text[0]
    data["tracks"]["security"]["skills"]["security"].append("Okta")
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Okta"):
        Profile.load(path)


# ---- taxonomy -----------------------------------------------------------------
@pytest.mark.parametrize("text,present,absent", [
    ("Experience with Node.js", "Node.js", "JavaScript"),
    ("Java and Spring Boot", "Java", "JavaScript"),
    ("GitHub Actions pipelines", "GitHub Actions", None),
    ("SOC 2 audits", "SOC 2", "SOC"),
    ("Work in our SOC as an analyst", "SOC", "SOC 2"),
    ("Azure AD and Entra ID", "Azure AD", "Azure"),
    ("MySQL databases", "MySQL", "SQL"),
])
def test_taxonomy_boundaries(text, present, absent):
    found = taxonomy.find_terms(text)
    assert present in found
    if absent:
        assert absent not in found


# ---- keywords -----------------------------------------------------------------
def test_iam_job_matches_security_track(profile):
    kw = keywords.match(profile, IAM_JD)
    assert kw.track == "security"
    assert {"Azure AD", "Active Directory", "SSO/MFA", "ServiceNow"} <= set(kw.matched)
    assert "SIEM" in kw.partial
    assert {"Okta", "SailPoint", "Splunk"} <= set(kw.missing)
    assert 40 < kw.score < 100


def test_software_job_matches_software_track(profile):
    kw = keywords.match(profile, SW_JD)
    assert kw.track == "software"
    assert {"React", "TypeScript", "Node.js", "CI/CD", "Unit testing"} <= set(kw.matched)
    assert "Docker" in kw.partial
    assert "PostgreSQL" in kw.missing


def test_empty_job_scores_zero(profile):
    kw = keywords.match(profile, "We are hiring. Apply now.")
    assert kw.score == 0 and kw.confidence == "low"


def test_match_resume_counts_literal_terms():
    assert keywords.match_resume("Python and React", "I write Python") == 50


# ---- fit rules ------------------------------------------------------------------
def test_bengaluru_role_in_salary_range_is_apply(profile):
    report = fit.check(profile, IAM_JD)
    assert report.verdict == fit.APPLY
    assert report.salary == "8-10 LPA" and report.salary_ok


def test_low_india_salary_is_skip(profile):
    report = fit.check(profile, "Analyst in Bengaluru. Salary 3-4 LPA.")
    assert report.verdict == fit.SKIP


def test_other_indian_city_needs_review(profile):
    assert fit.check(profile, "Engineer role", "Pune").verdict == fit.REVIEW


def test_remote_foreign_employer_needs_review(profile):
    report = fit.check(profile, SW_JD)
    assert report.verdict == fit.REVIEW and report.remote and report.abroad
    assert report.salary_ok


def test_abroad_without_sponsorship_is_skip(profile):
    report = fit.check(profile, "Security Engineer in London. Must have the right to work in the UK. £45,000.")
    assert report.verdict == fit.SKIP and report.sponsorship == "no"


def test_abroad_with_sponsorship_and_good_pay_is_apply(profile):
    report = fit.check(profile, "Cloud Security Engineer, Berlin, Germany. Visa sponsorship available. "
                                "1+ years of experience. Salary €60,000.")
    assert report.verdict == fit.APPLY and report.sponsorship == "yes" and report.salary_ok


def test_abroad_low_pay_is_skip(profile):
    report = fit.check(profile, "Junior analyst, Dublin, Ireland. Visa sponsorship available. €30,000.")
    assert report.verdict == fit.SKIP


def test_senior_role_is_skip(profile):
    assert fit.check(profile, "IAM Engineer, Bengaluru, 6+ years of experience required.").verdict == fit.SKIP


def test_pronoun_us_is_not_united_states(profile):
    assert not fit.check(profile, "Join us in Bengaluru! Tell us about yourself.").abroad


# ---- tracker -----------------------------------------------------------------------
def test_tracker_add_update_and_log(tmp_path):
    t = Tracker(tmp_path)
    row = t.add(company="Acme", role="IAM Analyst", url="https://x/1", location="Bengaluru", text=IAM_JD,
                track="security", keyword_score=70, fit="APPLY")
    assert row["id"] == "J0001" and t.jd_text("J0001") == IAM_JD
    assert t.find_url("https://x/1/")["id"] == "J0001"
    t.update("j0001", status="applied", notes="via careers page")
    assert t.get("J0001")["status"] == "completed-manual"
    log = " | ".join(f"{e['job_id']} {e['message']}" for e in t.events())
    assert "J0001 added" in log and "discovered -> completed-manual" in log
    second = t.add(company="B", role="R", url="", location="", text="x", track="software", keyword_score=1, fit="SKIP")
    assert second["id"] == "J0002"


def test_tracker_rejects_bad_status(tmp_path):
    t = Tracker(tmp_path)
    t.add(company="A", role="R", url="", location="", text="x", track="security", keyword_score=1, fit="APPLY")
    with pytest.raises(ValueError):
        t.update("J0001", status="hired!!")
    with pytest.raises(KeyError):
        t.get("J0099")


def test_gap_summary_ranks_missing_and_coursework_skills(tmp_path, profile):
    t = Tracker(tmp_path)
    for i in range(3):
        t.add(company=f"C{i}", role="IAM", url="", location="", text=IAM_JD, track="security",
              keyword_score=60, fit="APPLY")
    text = t.gap_summary(profile)
    assert "Okta | 3 | 100%" in text
    assert "SIEM | 3" in text
    assert t.gap_path.exists()
