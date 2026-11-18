"""Job sources, alert emails, the discovery pipeline and the dashboard (all offline)."""

import shutil
import sys
from email.message import EmailMessage

import pytest

from career import pipeline, sources
from career.profile import ROOT, Profile
from career.tracker import Tracker
from tests.conftest import project_ignore

JD =(ROOT / "tests" / "sample_jd_iam.txt").read_text(encoding="utf-8")

GREENHOUSE = {"jobs": [
    {"title": "IAM Analyst", "absolute_url": "https://boards.example.com/acme/jobs/1",
     "location": {"name": "Bengaluru, India"},
     "content": "&lt;p&gt;" + JD.replace("\n", "&lt;br&gt;") + "&lt;/p&gt;"},
    {"title": "Head Chef", "absolute_url": "https://boards.example.com/acme/jobs/2", "location": {"name": "Bengaluru"},
     "content": "Cook food."},
    {"title": "Senior Security Architect", "absolute_url": "https://boards.example.com/acme/jobs/3",
     "location": {"name": "Bengaluru"},
     "content": "Security architect role in Bengaluru. 10+ years of experience required with IAM, Azure AD, "
                "Zero Trust and cloud security across large enterprises. " * 3},
]}
LEVER = [{"text": "Junior Software Engineer", "hostedUrl": "https://jobs.example.com/beta/9",
          "categories": {"location": "Remote"},
          "descriptionPlain": "Build React and TypeScript features with Node.js and REST APIs. " * 5,
          "lists": [{"text": "Requirements", "content": "<li>Git and CI/CD</li><li>Unit testing</li>"}]}]


def alert_eml(sender: str, subject: str, html_body: str) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "me@example.com", subject
    msg.set_content("plain fallback")
    msg.add_alternative(html_body, subtype="html")
    return bytes(msg)


LINKEDIN_ALERT = alert_eml(
    "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "IAM Analyst: 2 new jobs",
    '<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&amp;refId=x">IAM Analyst</a>'
    '<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trk=2">View job</a>'
    '<a href="https://www.linkedin.com/comm/jobs/view/4099999999">Cloud Security Engineer</a>'
    '<a href="https://www.linkedin.com/feed/">Home</a>')
NAUKRI_ALERT = alert_eml(
    "Naukri <info@naukri.com>", "Jobs matching your profile",
    '<a href="https://www.naukri.com/job-listings-soc-analyst-acme-bengaluru-0-to-2-years-151124000123?src=alert">'
    'SOC Analyst</a>')


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


# ---- parsers --------------------------------------------------------------------------
def test_greenhouse_parser_unescapes_html():
    jobs = sources.parse_greenhouse(GREENHOUSE, "Acme")
    assert jobs[0].title == "IAM Analyst" and jobs[0].company == "Acme" and jobs[0].location == "Bengaluru, India"
    assert "Azure AD" in jobs[0].description and "<p>" not in jobs[0].description


def test_lever_parser_includes_requirement_lists():
    [job] = sources.parse_lever(LEVER, "Beta")
    assert job.url == "https://jobs.example.com/beta/9" and job.location == "Remote"
    assert "Unit testing" in job.description


def test_linkedin_alert_links_are_canonical_and_deduplicated():
    jobs = sources.parse_alert_eml(LINKEDIN_ALERT)
    assert [j.url for j in jobs] == ["https://www.linkedin.com/jobs/view/4012345678/",
                                     "https://www.linkedin.com/jobs/view/4099999999/"]
    assert jobs[0].title == "IAM Analyst" and jobs[0].source == "linkedin-alert"


def test_naukri_alert_link_drops_tracking():
    [job] = sources.parse_alert_eml(NAUKRI_ALERT)
    assert job.url.endswith("151124000123") and job.source == "naukri-alert" and job.title == "SOC Analyst"


def test_non_alert_email_gives_nothing():
    assert sources.parse_alert_eml(alert_eml("friend@example.com", "Hi", "<p>hello</p>")) == []


def test_careers_page_follows_matching_links_and_respects_robots():
    pages = {
        "https://careers.example.com/robots.txt": "User-agent: *\nDisallow: /private/",
        "https://careers.example.com/jobs": '<a href="/jobs/iam-analyst">IAM Analyst</a>'
                                            '<a href="/private/secret">Security Engineer</a>'
                                            '<a href="https://other.example.com/x">Security role elsewhere</a>'
                                            '<a href="/about">About us</a>',
        "https://careers.example.com/jobs/iam-analyst": "<h1>IAM Analyst</h1><p>" + JD + "</p>",
    }
    jobs, warnings = sources.careers_page("Acme", "https://careers.example.com/jobs", ["iam", "security"],
                                          read=pages.__getitem__, delay=0)
    assert [j.url for j in jobs] == ["https://careers.example.com/jobs/iam-analyst"]
    assert "Azure AD" in jobs[0].description


def test_careers_page_blocked_by_robots_is_skipped():
    pages = {"https://blocked.example.com/robots.txt": "User-agent: *\nDisallow: /"}
    jobs, warnings = sources.careers_page("X", "https://blocked.example.com/careers", ["engineer"],
                                          read=pages.__getitem__, delay=0)
    assert jobs == [] and "robots.txt" in warnings[0]


def test_unreadable_robots_means_no_crawl():
    def read(url):
        raise TimeoutError
    assert sources.allowed_by_robots("https://slow.example.com/jobs", read) is False


# ---- pipeline ---------------------------------------------------------------------------
def test_discover_filters_titles_and_reports_errors():
    cfg = {"title_keywords": ["analyst", "engineer", "architect"],
           "greenhouse": [{"board": "acme", "company": "Acme"}, {"board": "broken", "company": "Broken"}],
           "lever": [{"site": "beta", "company": "Beta"}]}

    def fetch_json(url):
        if "broken" in url:
            raise ConnectionError("down")
        return GREENHOUSE if "greenhouse" in url else LEVER
    jobs, notes = pipeline.discover(cfg, fetch_json=fetch_json, fetch_text=lambda u: "", delay=0)
    assert [j.title for j in jobs] == ["IAM Analyst", "Senior Security Architect", "Junior Software Engineer"]
    assert any("broken" in n for n in notes)


def test_ingest_scores_dedupes_and_applies_rules(tmp_path, profile):
    t = Tracker(tmp_path)
    jobs = sources.parse_greenhouse(GREENHOUSE, "Acme") + sources.parse_alert_eml(LINKEDIN_ALERT)
    counts = pipeline.ingest(t, profile, jobs)
    assert counts == {"new": 1, "duplicate": 0, "needs_description": 3, "auto_skipped": 1, "unsafe": 0}
    by_role = {r["role"]: r for r in t.rows()}
    assert by_role["IAM Analyst"]["status"] in ("discovered", "needs-description")
    assert by_role["Senior Security Architect"]["status"] == "skipped"
    assert pipeline.ingest(t, profile, jobs)["duplicate"] == len(jobs)


def test_attach_description_scores_an_alert_job(tmp_path, profile):
    t = Tracker(tmp_path)
    pipeline.ingest(t, profile, sources.parse_alert_eml(NAUKRI_ALERT))
    row = pipeline.attach_description(t, profile, "J0001", JD)
    assert row["status"] == "discovered" and row["fit"] == "APPLY" and int(row["keyword_score"]) > 40
    with pytest.raises(ValueError):
        pipeline.attach_description(t, profile, "J0001", "too short")


def test_read_inbox_moves_processed_files(tmp_path):
    (tmp_path / "a.eml").write_bytes(LINKEDIN_ALERT)
    (tmp_path / "b.eml").write_bytes(alert_eml("x@example.com", "Hello", "<p>no jobs</p>"))
    jobs, notes = sources.read_inbox(tmp_path)
    assert len(jobs) == 2 and len(notes) == 1
    assert not list(tmp_path.glob("*.eml")) and len(list((tmp_path / "processed").glob("*.eml"))) == 2


def test_marking_applied_records_the_date(tmp_path):
    t = Tracker(tmp_path)
    t.add(company="A", role="R", url="", location="", text="x", track="security", keyword_score=1, fit="APPLY")
    assert t.update("J0001", status="applied")["date_applied"]
    t.add(company="B", role="R", url="", location="", text="x", track="security", keyword_score=1, fit="APPLY")
    assert t.update("J0002", status="applied", date_applied="2026-11-01")["date_applied"] == "2026-11-01"


# ---- dashboard -------------------------------------------------------------------------------
@pytest.fixture
def project(tmp_path, monkeypatch):
    dst = tmp_path / "agent"
    shutil.copytree(ROOT, dst, ignore=project_ignore("inbox"))
    for mod in [m for m in sys.modules if m in ("jobs",) or m.startswith(("career", "ui"))]:
        del sys.modules[mod]
    monkeypatch.syspath_prepend(str(dst))
    yield dst
    for mod in [m for m in sys.modules if m in ("jobs",) or m.startswith(("career", "ui"))]:
        del sys.modules[mod]


@pytest.fixture
def client(project):
    from career import pipeline as pl
    from career import sources as src
    from career.profile import Profile as P
    from career.tracker import Tracker as T
    from ui.app import create_app

    t = T(project)
    pl.ingest(t, P.load(project / "profile" / "baseline.example.yaml"),
              src.parse_greenhouse(GREENHOUSE, "Acme") + src.parse_alert_eml(NAUKRI_ALERT))
    app = create_app(project)
    app.config["TESTING"] = True
    return app.test_client()


def test_pages_render(client):
    for url in ["/", "/jobs", "/jobs?status=discovered", "/needs", "/settings", "/activity?level=run",
                "/jobs?fit=SKIP", "/job/J0001", "/job/J0003", "/gaps", "/activity"]:
        resp = client.get(url)
        assert resp.status_code == 200, url
    home = client.get("/").get_data(as_text=True)
    assert "Best new matches" in home and "IAM Analyst" in home and "Need the job description" in home
    assert client.get("/job/J9999").status_code == 404


def test_learn_next_page_is_grouped_not_a_markdown_dump(client):
    page = client.get("/gaps").get_data(as_text=True)
    assert "Start here" in page and "How to use this" in page and "jobs" in page
    assert "|---|" not in page and "<pre>" not in page
    assert page.count('class="card pick"') <= 3


def test_job_page_shows_keywords_and_actions(client):
    page = client.get("/job/J0001").get_data(as_text=True)
    assert "You have:" in page and "Azure AD" in page and "Generate packet" in page and "Mark applied" in page


def test_paste_description_in_ui(client, project):
    from career.tracker import Tracker as T
    job_id = next(r["id"] for r in T(project).rows() if r["source"] == "naukri-alert")
    page = client.get(f"/job/{job_id}").get_data(as_text=True)
    assert "Paste the job description" in page
    resp = client.post(f"/job/{job_id}/attach", data={"text": JD}, follow_redirects=True)
    assert "Saved. Keyword" in resp.get_data(as_text=True)


def test_mark_applied_and_status(client, project):
    from career.tracker import Tracker as T
    client.post("/job/J0001/applied", data={"date": "2026-11-15"})
    assert T(project).get("J0001")["date_applied"] == "2026-11-15"
    bad = client.post("/job/J0001/applied", data={"date": "15/11"}, follow_redirects=True)
    assert "Use a date like" in bad.get_data(as_text=True)
    client.post("/job/J0001/status", data={"status": "interview", "note": "HR call"})
    assert T(project).get("J0001")["status"] == "interview"
    assert "2026-11-15" not in client.get("/jobs").get_data(as_text=True)  # applied jobs leave the Open tab
    jobs_page = client.get("/jobs?tab=applied").get_data(as_text=True)
    assert "2026-11-15" in jobs_page


def test_packet_button_runs_cli_and_files_are_served(client, project, monkeypatch):
    import ui.app as ui_app
    calls = []

    def fake_cli(root, *args, **kw):
        calls.append(args)
        import jobs
        return jobs.main(list(args)), "ok"
    monkeypatch.setattr(ui_app, "run_cli", fake_cli)
    monkeypatch.chdir(project)
    client.post("/job/J0001/packet", data={"offline": "1"})
    assert calls == [("apply", "J0001", "--offline")]
    assert client.get("/job/J0001/file/resume.pdf").status_code == 200
    assert client.get("/job/J0001/file/answers.md").status_code == 200
    assert client.get("/job/J0001/file/secret.txt").status_code == 404
    assert client.get("/job/J0001/file/..%2F.env").status_code == 404


def test_discover_and_ingest_buttons(client, monkeypatch):
    import ui.app as ui_app
    monkeypatch.setattr(ui_app, "run_cli", lambda root, *a, **k: (0, f"ran {' '.join(a)}"))
    assert "ran discover" in client.post("/discover", follow_redirects=True).get_data(as_text=True)
    assert "ran ingest-inbox" in client.post("/ingest", follow_redirects=True).get_data(as_text=True)


def test_cli_discovery_commands(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    import jobs
    # Use an empty sources config for this test, regardless of what the shipped
    # config/sources.yaml contains -- this test is about the CLI plumbing
    # (ingest-inbox / discover / attach / apply / applied), not about real companies.
    (project / "config" / "sources.yaml").write_text(
        "title_keywords: [security, engineer, software]\ngreenhouse: []\nlever: []\ncareers_pages: []\n",
        encoding="utf-8",
    )
    (project / "inbox").mkdir(exist_ok=True)
    (project / "inbox" / "alert.eml").write_bytes(LINKEDIN_ALERT)
    assert jobs.main(["ingest-inbox"]) == 0
    assert jobs.main(["discover"]) == 0
    jd_file = project / "jd.txt"
    jd_file.write_text(JD, encoding="utf-8")
    assert jobs.main(["attach", "J0001", "--file", str(jd_file)]) == 0
    assert jobs.main(["apply", "J0002"]) == 1  # still needs a description
    assert jobs.main(["applied", "J0001", "--date", "2026-11-10"]) == 0
    out = capsys.readouterr().out
    assert "2 job link(s)" in out and "No sources configured" in out and "marked applied on 2026-11-10" in out
