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

SOFTWARE_JD = ("Software Engineer. Build REST APIs in Java and React. Experience with IAM, SSO, Burp Suite and "
               "vulnerability management is a plus.")


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


def test_software_resume_shows_no_security_skills_even_when_the_job_mentions_them(profile):
    content = base_content(profile, "software")
    content["headline"] = "Software Engineer | IAM | Burp Suite"
    add_supported_skills(profile, content, SOFTWARE_JD)
    removed = focus_skills(profile, content, SOFTWARE_JD, "software")
    shown = [n for names in content["skills"].values() for n in names]
    assert "security" not in content["skills"] and "Burp Suite" in removed
    assert not [n for n in shown if "security" in {taxonomy.category(t) for t in taxonomy.find_terms(n)}]
    assert content["headline"] == profile.tracks["software"]["headline"]
    assert "React" in shown and content["skills"]["languages"] == ["Java"]


def test_security_resume_keeps_security_skills(profile):
    content = base_content(profile, "security")
    focus_skills(profile, content, SOFTWARE_JD, "security")
    assert "Burp Suite" in [n for names in content["skills"].values() for n in names]


def test_prompt_and_tailored_packet_follow_the_rules(tmp_path, profile):
    prompt = analyze.build_prompt(profile, "software", SOFTWARE_JD, analyze.keywords.match(profile, SOFTWARE_JD))
    assert "do not mention security" in prompt and "list only Python" in prompt
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
        [langs] = [line for line in text.splitlines() if line.startswith("Languages")]
        assert "Java" in langs and "PowerShell" not in langs and "Python" not in langs
