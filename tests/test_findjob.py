"""LinkedIn / Naukri jobs found on the company's own public board: never opening LinkedIn, close matches only."""

import pytest

from career import findjob, jdfetch
from career.profile import Profile
from career.tracker import Tracker

BODY = ("<p>About the role</p><p>You will build REST APIs in Python for our payments platform.</p>"
        "<p>Responsibilities: design, code review, testing.</p><p>Qualifications: 0-2 years, Git, CI/CD.</p>") * 3


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def greenhouse(jobs):
    return {"jobs": [{"id": i + 1, "title": t, "location": {"name": loc}, "content": BODY,
                      "absolute_url": f"https://acme.example/jobs/{i + 1}"} for i, (t, loc) in enumerate(jobs)]}


def feeds(board_jobs, asked=None):
    def fetch_json(url):
        if asked is not None:
            asked.append(url)
        assert "linkedin" not in url and "naukri" not in url
        if "boards-api.greenhouse.io/v1/boards/phonepe/" in url:
            return greenhouse(board_jobs)
        raise OSError("no such board")
    return fetch_json


def test_board_names_from_company_names():
    assert findjob.slugs("PhonePe Pvt. Ltd.")[0] == "phonepe"
    assert "medianet" in findjob.slugs("Media.net")
    assert findjob.slugs("Phone Pe")[:2] == ["phonepe", "phone-pe"]


def test_typos_and_sde_still_match_but_other_roles_do_not():
    assert findjob.similarity("Software Enigneer", "Software Engineer") > 0.95
    assert findjob.similarity("SDE", "Software Development Engineer") == 1
    assert findjob.similarity("Software Engineer", "Engineering Manager") < findjob.MIN_SIMILARITY


def test_finds_the_same_job_in_an_allowed_place():
    fetch = feeds([("Software Engineer", "London, UK"), ("Software Engineer", "Bengaluru, India"),
                   ("Data Scientist", "Bengaluru, India")])
    match = findjob.find("Phone Pe", "Software Enigneer", "Bangalore", ["india", "bengaluru", "remote"],
                         fetch_json=fetch)
    assert match and match.location == "Bengaluru, India" and match.board == "greenhouse:phonepe"
    assert match.apply_url == "https://job-boards.greenhouse.io/phonepe/jobs/2"


@pytest.mark.parametrize("company, title", [("Phone Pe", "Product Manager"), ("Unknown (from alert)", "SDE"),
                                            ("Phone Pe", "(title in the alert email; open the link)")])
def test_no_match_means_no_guess(company, title):
    fetch = feeds([("Software Engineer", "Bengaluru, India")])
    assert findjob.find(company, title, "", ["india"], fetch_json=fetch) is None


def add_alert(t, role, n=1):
    return t.add(company="Unknown (from alert)", role=role, url=f"https://www.linkedin.com/jobs/view/{n}/",
                 location="Bangalore", text="stub", track="software", keyword_score=0, fit="",
                 status="needs-description", source="linkedin-alert")


def never(*args, **kwargs):
    raise AssertionError("LinkedIn must never be fetched")


def test_alert_jobs_get_the_company_posting_and_its_form(tmp_path, profile):
    t = Tracker(tmp_path)
    add_alert(t, "Phone Pe is hiring Software Enigneer")
    seen = []

    def find(company, title, location, places, cache=None):
        seen.append((company, title))
        return findjob.find(company, title, location, ["india"], fetch_json=feeds([("Software Engineer",
                                                                                     "Bengaluru, India")]))
    counts = jdfetch.fill_missing(t, profile, fetch=never, sleep=lambda s: None, say=lambda *_: None,
                                  find_on_board=find)
    row = t.get("J0001")
    assert seen == [("Phone Pe", "Software Enigneer")] and counts["on_company_board"] == 1
    assert row["status"] in ("discovered", "skipped") and row["role"] == "Software Engineer"
    assert row["apply_url"] == "https://job-boards.greenhouse.io/phonepe/jobs/1"
    assert "greenhouse:phonepe" in row["notes"]
    assert "Responsibilities" in (t.jd_dir / "J0001.txt").read_text(encoding="utf-8")


def test_a_job_already_saved_from_the_board_is_not_added_twice(tmp_path, profile):
    t = Tracker(tmp_path)
    t.add(company="PhonePe", role="Software Engineer", url="https://acme.example/jobs/1", location="Bengaluru",
          text=BODY, track="software", keyword_score=60, fit="APPLY", status="discovered", source="greenhouse")
    add_alert(t, "Phone Pe is hiring Software Engineer", n=2)
    find = lambda *a, **k: findjob.find("Phone Pe", "Software Engineer", fetch_json=feeds(  # noqa: E731
        [("Software Engineer", "Bengaluru, India")]))
    jdfetch.fill_missing(t, profile, fetch=never, sleep=lambda s: None, say=lambda *_: None, find_on_board=find)
    assert t.get("J0002")["status"] == "skipped" and "Same job as J0001" in t.get("J0002")["notes"]


def test_not_found_is_not_searched_again_every_run(tmp_path, profile):
    t = Tracker(tmp_path)
    add_alert(t, "Junior Automation Specialist")
    calls = []
    find = lambda *a, **k: calls.append(a) or None  # noqa: E731
    for _ in range(2):
        jdfetch.fill_missing(t, profile, fetch=never, sleep=lambda s: None, say=lambda *_: None, find_on_board=find)
    assert len(calls) == 1 and t.get("J0001")["status"] == "needs-description"
