"""Job summary, honest ATS help, market pay, Telegram exports, Gmail (app password) and the Jobs page."""

import copy
import imaplib
import json
from datetime import datetime

import pytest

from career import fit, forms, gmail, jdsummary, keywords, marketpay, packet, sources
from career.profile import ROOT, Profile
from career.resume import base_content
from career.tracker import Tracker
from tests.fakes import FakeLLM, fake_answer

JD = (ROOT / "tests" / "sample_jd_iam.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


# ---- job summary ----------------------------------------------------------------------------------
POSTING = """Security Analyst, Bengaluru. 2+ years of experience.
About us
We are a fast-growing company with a great culture.
What you'll do:
- Monitor SIEM alerts and triage incidents
- Manage Azure AD access reviews
Requirements
- 2+ years in a SOC or IAM role
- Strong PowerShell
You thrive in ambiguity and love solving hard problems with your team every day.
Nice to have
- Okta or SailPoint
Benefits
- Health insurance
"""


def test_sections_pick_bullets_and_skip_culture_and_benefits():
    found = jdsummary.sections(POSTING)
    assert found["duties"] == ["Monitor SIEM alerts and triage incidents", "Manage Azure AD access reviews"]
    assert found["required"] == ["2+ years in a SOC or IAM role", "Strong PowerShell"]
    assert found["nice"] == ["Okta or SailPoint"]  # "Health insurance" is under Benefits: left out


def test_summary_one_line(profile):
    s = jdsummary.summarize(profile, POSTING, "Bengaluru", "Security Analyst")
    assert s.years == "2+ years" and "Azure AD" in s.skills_have and "SIEM" in s.skills_coursework
    assert s.one_line.startswith("Experience: 2+ years · Pay: not stated · Key skills:")


# ---- ATS: only skills you can prove are added ---------------------------------------------------------
def test_add_supported_skills_is_honest(profile):
    content = base_content(profile, "software")  # the software track lists few security skills
    added = keywords.add_supported_skills(profile, content, JD)
    listed = {n for names in content["skills"].values() for n in names}
    assert "Azure AD" in added and "Azure AD" in listed
    assert "Okta" not in listed and "SailPoint" not in listed  # asked for, but not in the profile
    assert "SIEM" in content["skills"].get("familiar", [])  # coursework only: under "Familiar with"
    assert keywords.add_supported_skills(profile, content, JD) == []  # nothing twice


# ---- market pay and the expected-salary answer -----------------------------------------------------------
def market(low, high, unit="LPA"):
    return marketpay._valid({"low": low, "high": high, "unit": unit, "basis": "test", "confidence": "medium"})


def test_expected_salary_uses_market_never_below_floor(profile):
    home = fit.check(profile, JD, "Bengaluru")
    assert forms.expected_salary(profile, home, market(8, 12)) == "10 LPA (open to discussion)"
    assert forms.expected_salary(profile, home, market(3, 5)) == "6 LPA (open to discussion)"  # floor 6 wins
    assert forms.expected_salary(profile, home, None) == profile.preferences["expected_salary"]
    abroad = fit.check(profile, "Engineer in London, UK. Visa sponsorship available.", "London, UK")
    assert forms.expected_salary(profile, abroad, market(30000, 36000, "GBP")).startswith("GBP 40,000 per year")


@pytest.mark.parametrize("answer", [{"low": 9, "high": 5, "unit": "LPA"}, {"low": "a", "high": 5, "unit": "LPA"},
                                    {"low": 5, "high": 900, "unit": "LPA"}, {"low": 5, "high": 7}])
def test_market_estimates_that_make_no_sense_are_dropped(answer):
    assert marketpay._valid(answer) is None


def test_packet_improves_low_ats_and_saves_market_pay(tmp_path, profile):
    t = Tracker(tmp_path)
    t.add(company="Acme", role="IAM Analyst", url="https://jobs.lever.co/acme/1", location="Bengaluru", text=JD,
          track="security", keyword_score=70, fit="APPLY")
    better = {"changes_made": ["Led with Azure AD"], "not_done": [],
              "steps4to7_tailored_resume": copy.deepcopy(fake_answer(False)["steps4to7_tailored_resume"]),
              "step8_ats_after": {"overall": 92}}
    pay = {"low": 6, "high": 9, "unit": "LPA", "basis": "AmbitionBox, entry level", "confidence": "medium"}
    llm = FakeLLM([fake_answer(True), fake_answer(False), better, pay])
    said = []
    assert packet.build_packet(t, profile, "J0001", say=said.append, llm=llm) == packet.OK
    assert any("under your target of 85" in s for s in said) and any("Improved: ATS 84 -> 92" in s for s in said)
    row = t.get("J0001")
    assert row["ats_after"] == "92" and row["market_pay"] == "6-9 LPA"
    folder = t.root / row["output_dir"]
    saved = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert saved["market_pay"]["searched"] is False  # the fake has no web search: labelled as such
    assert "7.5 LPA (open to discussion)" in (folder / "answers.md").read_text(encoding="utf-8")


# ---- Telegram exports ------------------------------------------------------------------------------------
def telegram_export(now="2026-11-17T10:00:00"):
    long_post = ("🚀 Hiring: SOC Analyst\nCompany: Acme Security\nLocation: Bengaluru\n"
                 + "Monitor SIEM alerts, triage incidents and run Azure AD access reviews. " * 4)
    return {"name": "Cyber Jobs India", "messages": [
        {"id": 1, "type": "message", "date": now,
         "text": [long_post, {"type": "text_link", "text": "Apply here", "href": "https://jobs.lever.co/acme/7"}]},
        {"id": 2, "type": "message", "date": now,
         "text": "Today's openings:\n1. IAM Engineer at Beta - https://boards.greenhouse.io/beta/jobs/9\n"
                 "2. Cloud Analyst at Gamma https://www.linkedin.com/jobs/view/123456/?ref=tg\n"
                 "Join us: https://t.me/cyberjobs"},
        {"id": 3, "type": "message", "date": "2026-09-01T10:00:00", "text": "Old: https://example.com/old-job"},
        {"id": 4, "type": "service", "date": now, "action": "join_group"},
    ]}


def test_telegram_export_becomes_jobs():
    jobs = {j.url: j for j in sources.parse_telegram_export(telegram_export(), today=datetime(2026, 11, 17))}
    assert set(jobs) == {"https://jobs.lever.co/acme/7", "https://boards.greenhouse.io/beta/jobs/9",
                         "https://www.linkedin.com/jobs/view/123456/"}  # t.me and old posts left out
    soc = jobs["https://jobs.lever.co/acme/7"]
    assert (soc.title, soc.company, soc.location, soc.source) == ("SOC Analyst", "Acme Security", "Bengaluru",
                                                                  "telegram")
    assert len(soc.description) > 200  # a full post is scored straight away
    assert jobs["https://boards.greenhouse.io/beta/jobs/9"].title == "IAM Engineer at Beta"
    assert jobs["https://www.linkedin.com/jobs/view/123456/"].source == "linkedin-alert"  # never automated


def test_inbox_reads_telegram_exports_and_moves_them(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps(telegram_export(datetime.now().isoformat()[:19])),
                                          encoding="utf-8")
    (tmp_path / "other.json").write_text("[1, 2]", encoding="utf-8")
    jobs, notes = sources.read_inbox(tmp_path)
    assert len(jobs) == 3 and any("other.json: not a Telegram chat export" in n for n in notes)
    assert not (tmp_path / "result.json").exists() and list((tmp_path / "processed").glob("*result.json"))


# ---- Gmail with an app password (no real connection) ----------------------------------------------------------
class FakeIMAP:
    calls: list = []

    def __init__(self, host):
        FakeIMAP.calls = [("connect", host)]

    def login(self, user, password):
        if password != "abcdefghijklmnop":
            raise imaplib.IMAP4.error("bad credentials")
        self.calls.append(("login", user))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        return "OK", [b"1 2"]

    def fetch(self, msg_id, what):
        self.calls.append(("fetch", msg_id, what))
        return "OK", [(b"1 (BODY[] {5}", b"raw" + msg_id), b")"]

    def logout(self):
        self.calls.append(("logout",))


def test_imap_is_read_only_and_finds_alerts_by_sender():
    raws = gmail.fetch_via_imap("me@gmail.com", "abcd efgh ijkl mnop", connect=FakeIMAP)
    assert raws == [b"raw1", b"raw2"]
    assert ("select", '"[Gmail]/All Mail"', True) in FakeIMAP.calls  # opened read-only
    assert all(c[2] == "(BODY.PEEK[])" for c in FakeIMAP.calls if c[0] == "fetch")  # never marks as read
    query = next(c for c in FakeIMAP.calls if c[0] == "search")[1]
    assert query[0] == "X-GM-RAW" and "jobalerts-noreply@linkedin.com" in query[1] and "newer_than:3d" in query[1]
    with pytest.raises(gmail.GmailError, match="app password"):
        gmail.fetch_via_imap("me@gmail.com", "wrong", connect=FakeIMAP)
