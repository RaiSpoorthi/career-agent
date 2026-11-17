"""Anyone can use this: personal files stay out of git, setup works from the examples, and rules
that used to assume one person (home country, track names) come from the profile."""

import copy
import shutil

import pytest
import yaml

from career import fit, forms, keywords
from career.profile import EXAMPLES_ENV, ROOT, Profile, personal_file, profile_path
from tests.conftest import project_ignore


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def changed(profile, **prefs):
    clone = copy.deepcopy(profile)
    clone.data["preferences"].update(prefs)
    return clone


def test_tests_never_read_personal_files():
    assert profile_path().name == "baseline.example.yaml"
    assert personal_file("config/answers.yaml").name == "answers.example.yaml"
    assert Profile.load().contact["name"] == "Alex Morgan"


def test_personal_files_are_gitignored_and_examples_are_not():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for rel in ("profile/baseline.yaml", "config/answers.yaml", "config/sources.yaml", ".env", "CLAUDE.local.md"):
        assert rel in ignored
    for rel in ("profile/baseline.example.yaml", "config/answers.example.yaml", "config/sources.example.yaml"):
        assert (ROOT / rel).exists() and rel not in ignored


def test_new_user_without_profile_is_told_to_run_init(tmp_path, monkeypatch):
    monkeypatch.delenv(EXAMPLES_ENV)
    with pytest.raises(FileNotFoundError, match="jobs.py init"):
        profile_path(tmp_path)


def test_sample_profile_is_refused_outside_examples_mode(monkeypatch):
    monkeypatch.delenv(EXAMPLES_ENV)
    with pytest.raises(ValueError, match="sample person"):
        Profile.load(ROOT / "profile" / "baseline.example.yaml")


def test_init_creates_missing_files_and_keeps_existing_ones(tmp_path, monkeypatch, capsys):
    import jobs
    project = tmp_path / "agent"
    for folder in ("profile", "config"):
        shutil.copytree(ROOT / folder, project / folder, ignore=project_ignore())
    shutil.copyfile(ROOT / ".env.example", project / ".env.example")
    (project / ".env").write_text("GEMINI_API_KEY=mine\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "ROOT", project)
    assert jobs.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "created  profile/baseline.yaml" in out and "kept     .env" in out
    assert (project / ".env").read_text(encoding="utf-8") == "GEMINI_API_KEY=mine\n"
    assert yaml.safe_load((project / "profile" / "baseline.yaml").read_text(encoding="utf-8"))["sample"] is True


def test_bare_command_prints_help(capsys):
    import jobs
    assert jobs.main([]) == 0
    assert "init" in capsys.readouterr().out


# ---- rules come from the profile, not from one person's situation ----------------------------------
def test_home_country_other_than_india(profile):
    berlin = changed(profile, home_country="Germany", home_places=["berlin", "munich", "germany"],
                     ok_locations=["berlin", "remote"], needs_visa_sponsorship_abroad=True)
    berlin.data["contact"] = dict(profile.contact, location="Berlin, Germany")
    assert fit.check(berlin, "Engineer in Berlin, Germany. 1 year of experience.", "Berlin").verdict == fit.APPLY
    munich = fit.check(berlin, "Engineer in Munich. 1 year of experience.", "Munich, Germany")
    assert munich.verdict == fit.REVIEW and not munich.abroad and "not Berlin/Remote" in munich.reasons[0]
    bengaluru = fit.check(berlin, "Engineer in Bengaluru. We do not sponsor visas.", "Bengaluru, India")
    assert bengaluru.abroad and bengaluru.verdict == fit.SKIP and "outside Germany" in bengaluru.reasons[0]
    answers = forms.answers_markdown(berlin, {"role": "Engineer"}, fit.check(berlin, "Berlin role", "Berlin"))
    assert "No (for roles in Germany)" in answers and "Based in Berlin, Germany" in answers


def test_home_country_defaults_to_contact_location(profile):
    clone = copy.deepcopy(profile)
    clone.data["preferences"].pop("home_country")
    assert fit.home_country(clone) == "India"


def test_salary_rules_are_optional(profile):
    no_floors = changed(profile, salary_min={}, salary_min_lpa=None, expected_salary=None)
    report = fit.check(no_floors, "Analyst, Bengaluru. CTC 3 LPA. $20,000 for remote.", "Bengaluru")
    assert report.salary_ok is None and report.verdict == fit.APPLY
    assert forms.expected_salary(no_floors, report) == "Open to discussion"


def test_any_track_names_work(profile):
    renamed = copy.deepcopy(profile)
    tracks = renamed.data["tracks"]
    tracks["frontend"] = dict(tracks.pop("software"), categories=["software"])
    tracks["defense"] = dict(tracks.pop("security"), categories=["security"])
    assert keywords.match(renamed, "React, TypeScript, Node.js, REST APIs, unit testing").track == "frontend"
    assert keywords.match(renamed, "SIEM, SOC, incident response, Azure AD, MFA").track == "defense"
    assert keywords.match(renamed, "Excellent communication").track == "frontend"  # no votes: first track
