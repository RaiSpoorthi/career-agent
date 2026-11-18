"""Skills section stays on target: only the languages a job asks for, and no hidden categories on a track."""

import copy

import pytest
import yaml
from docx import Document

from career import analyze, packet, taxonomy
from career.focus import focus_skills
from career.keywords import add_supported_skills
from career.profile import Profile
from career.resume import base_content
from career.tracker import Tracker
from tests.fakes import FakeLLM, fake_answer

SOFTWARE_JD = "Software Engineer. Build REST APIs in Java and React, with Git and CI/CD."
BOTH_JD = SOFTWARE_JD + " Experience with IAM, SSO, Burp Suite and vulnerability management is required."


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def languages(profile, job_text, track="software"):
    content = base_content(profile, track)
    focus_skills(profile, content, job_text, track)
    return content["skills"].get("languages")


def test_only_the_languages_the_job_asks_for(profile):
    assert languages(profile, "We use Python daily.") == ["Python"]
    assert languages(profile, "Backend in Java, reporting in SQL.") == ["SQL", "Java"]  # profile order
    assert languages(profile, "JavaScript is not the same as Java here: we want Java.") == ["JavaScript", "Java"]


def test_default_language_when_the_job_names_none(profile):
    assert languages(profile, "Great communication and ownership.") == ["Python"]
    assert languages(profile, "Great communication.", track="security") == ["Python"]


def test_languages_untouched_when_the_rule_is_off(profile, tmp_path):
    data = copy.deepcopy(profile.data)
    data["preferences"].pop("languages")
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    plain = Profile.load(tmp_path / "p.yaml")
    assert languages(plain, "Nothing here.") == base_content(plain, "software")["skills"]["languages"]


def shown(content):
    return [n for names in content["skills"].values() for n in names]


def is_security(name):
    return "security" in {taxonomy.category(t) for t in taxonomy.find_terms(name)}


def test_software_resume_shows_no_security_skills(profile):
    content = base_content(profile, "software")
    content["headline"] = "Software Engineer | IAM | Burp Suite"
    add_supported_skills(profile, content, SOFTWARE_JD)
    removed = focus_skills(profile, content, SOFTWARE_JD, "software")
    assert "security" not in content["skills"] and "Burp Suite" in removed
    assert not [n for n in shown(content) if is_security(n)]
    assert content["headline"] == profile.tracks["software"]["headline"]
    assert "React" in shown(content) and content["skills"]["languages"] == ["Java"]


def test_security_skills_the_job_explicitly_asks_for_stay_on_a_software_resume(profile):
    content = base_content(profile, "software")
    add_supported_skills(profile, content, BOTH_JD)
    focus_skills(profile, content, BOTH_JD, "software")
    security = [n for n in shown(content) if is_security(n)]
    assert {"Identity & Access Management", "SSO/MFA", "Burp Suite"} <= set(security)
    assert "Kali Linux" not in security and "Zero Trust" not in security  # not asked: still left out


def test_skills_are_capped_with_the_jobs_asks_first(profile):
    content = base_content(profile, "security")
    content["skills"]["familiar"] = ["SIEM", "Docker", "Kubernetes"]
    focus_skills(profile, content, "Security analyst: SIEM, Nmap, Wireshark and Python.", "security")
    names = shown(content)
    assert len(names) <= profile.preferences["max_skills"]
    assert all(len(v) <= profile.preferences["max_per_group"] for v in content["skills"].values())
    assert {"Nmap", "Wireshark", "Python"} <= set(names)
    assert content["skills"]["familiar"] == ["SIEM"]  # coursework only when the job asks for it


def test_no_title_line_under_the_name_when_turned_off(profile):
    from career.resume import layout
    assert layout(profile, base_content(profile, "software"))["headline"] == ""


def test_security_resume_keeps_security_skills(profile):
    content = base_content(profile, "security")
    focus_skills(profile, content, BOTH_JD, "security")
    assert {"Identity & Access Management", "Burp Suite"} <= set(shown(content))


def test_prompt_and_tailored_packet_follow_the_rules(tmp_path, profile):
    prompt = analyze.build_prompt(profile, "software", SOFTWARE_JD, analyze.keywords.match(profile, SOFTWARE_JD))
    assert "leave out security" in prompt and "list only Python" in prompt and "at most 14 skills" in prompt
    t = Tracker(tmp_path)
    for offline in (True, False):
        row = t.add(company="Acme", role="Software Engineer", location="Bengaluru", text=SOFTWARE_JD,
                    url=f"https://job-boards.greenhouse.io/acme/jobs/{offline}", track="software",
                    keyword_score=70, fit="APPLY")
        assert packet.build_packet(t, profile, row["id"], offline=offline, track="software", say=lambda *_: None,
                                   llm=FakeLLM([fake_answer(False)])) == packet.OK
        doc = Document(str(t.root / t.get(row["id"])["output_dir"] / "resume.docx"))
        text = "\n".join(par.text for par in doc.paragraphs)
        assert "Burp Suite" not in text and "Kali Linux" not in text and "Zero Trust" not in text
        assert profile.tracks["software"]["headline"] not in text
        [langs] = [line for line in text.splitlines() if line.startswith("Languages")]
        assert "Java" in langs and "PowerShell" not in langs and "Python" not in langs
