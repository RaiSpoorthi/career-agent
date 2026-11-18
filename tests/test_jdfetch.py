"""Full descriptions for title-only links: public job data only, never account sites or unsafe links."""

import pytest

from career import jdfetch
from career.profile import Profile
from career.tracker import Tracker

BODY = ("<p>About the role</p><p>You will build REST APIs in Python and React for our payments platform.</p>"
        "<p>Responsibilities: design, code review, testing and on-call with the team.</p>"
        "<p>Qualifications: 0-2 years of experience, strong problem solving, Git and CI/CD.</p>") * 3


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def never(*args, **kwargs):
    raise AssertionError("must not be fetched")


def test_workday_and_oracle_use_their_public_job_data():
    asked = []

    def read_json(url):
        asked.append(url)
        if "wday/cxs" in url:
            return {"jobPostingInfo": {"title": "Software Engineer", "location": "Bengaluru", "jobDescription": BODY}}
        return {"items": [{"Title": "Software Engineer I", "PrimaryLocation": "Bengaluru",
                           "ExternalDescriptionStr": BODY}]}
    wd = jdfetch.fetch_description("https://hp.wd5.myworkdayjobs.com/en-US/ExternalCareerSite/job/Bangalore/SE_123",
                                   read=never, read_json=read_json, robots=never)
    oracle = jdfetch.fetch_description("https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/"
                                       "job/26015127", read=never, read_json=read_json, robots=never)
    assert asked[0] == "https://hp.wd5.myworkdayjobs.com/wday/cxs/hp/ExternalCareerSite/job/Bangalore/SE_123"
    assert "recruitingCEJobRequisitionDetails" in asked[1] and "26015127" in asked[1]
    assert wd.startswith("Software Engineer\nBengaluru") and "Responsibilities" in oracle


def test_other_pages_only_when_robots_allows_and_without_scripts():
    page = "<script>window.x = {a: 1};</script><style>.a{}</style>" + BODY
    text = jdfetch.fetch_description("https://careers.acme.com/job/1", read=lambda u: page, read_json=never,
                                     robots=lambda u, read: True)
    assert "window" not in text and "Responsibilities" in text
    assert jdfetch.fetch_description("https://careers.acme.com/job/1", read=lambda u: page, read_json=never,
                                     robots=lambda u, read: False) == ""


@pytest.mark.parametrize("page", ["<p>Sign in to continue</p>" * 60,  # not a job description
                                  "<h1>Page not found</h1>" + BODY])  # an error page
def test_pages_that_are_not_job_descriptions_are_rejected(page):
    assert jdfetch.fetch_description("https://careers.acme.com/job/1", read=lambda u: page, read_json=never,
                                     robots=lambda u, read: True) == ""


@pytest.mark.parametrize("url", ["https://www.linkedin.com/jobs/view/123/", "https://www.naukri.com/job-1",
                                 "https://bit.ly/abc", "https://forms.gle/x", "http://careers.acme.com/1"])
def test_account_sites_and_unsafe_links_are_never_fetched(url):
    assert jdfetch.fetch_description(url, read=never, read_json=never, robots=never) == ""


@pytest.mark.parametrize("role, company, text, expect", [
    ("Barclays is hiring AI Engineer", "", "", {"company": "Barclays", "role": "AI Engineer"}),
    ("Criteo is hiring a Senior Software Engineer", "", "", {"company": "Criteo", "role": "Senior Software Engineer"}),
    ("Apply here", "", "window.x = 1;\nData Engineer | Citi Careers\nAbout", {"role": "Data Engineer"}),
    ("Software Engineer", "Acme", "anything", {}),
])
def test_better_names(role, company, text, expect):
    assert jdfetch.better_names(role, company, text) == expect


def test_fill_missing_scores_found_jobs_and_does_not_retry_the_rest(tmp_path, profile):
    t = Tracker(tmp_path)
    for n, role in enumerate(("Acme is hiring Software Engineer", "Apply here")):
        t.add(company="Unknown (from alert)", role=role, url=f"https://careers.acme.com/{n}", location="Bengaluru",
              text="stub", track="software", keyword_score=0, fit="", status="needs-description", source="telegram")
    calls = []

    def fetch(url):
        calls.append(url)
        return jdfetch.sources.html_to_text(BODY) if url.endswith("/0") else ""
    assert jdfetch.fill_missing(t, profile, fetch=fetch, sleep=lambda s: None, say=lambda *_: None) == \
        {"found": 1, "not_found": 1}
    found, missing = t.get("J0001"), t.get("J0002")
    assert found["status"] in ("discovered", "skipped") and found["company"] == "Acme"
    assert found["role"] == "Software Engineer" and missing["status"] == "needs-description"
    jdfetch.fill_missing(t, profile, fetch=fetch, sleep=lambda s: None, say=lambda *_: None)
    assert calls.count("https://careers.acme.com/1") == 1  # not fetched again on the next run
