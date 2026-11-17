"""Location rules, pay, re-scoring, tailoring REVIEW jobs automatically, and revising a resume on request."""

import copy
import json

import pytest

from career import agent, autoapply, fit, packet, pipeline
from career.profile import ROOT, Profile
from career.tracker import Tracker
from tests.fakes import FakeLLM, fake_answer

JD = (ROOT / "tests" / "sample_jd_iam.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def with_prefs(profile, **prefs):
    clone = copy.deepcopy(profile)
    clone.data["preferences"].update(prefs)
    return clone


def add_job(t, location="Bengaluru", text=JD, **kw):
    base = dict(company="Acme", role="IAM Analyst", url=f"https://job-boards.greenhouse.io/acme/jobs/{len(t.rows())}",
                location=location, text=text, track="security", keyword_score=75, fit="APPLY", source="greenhouse")
    return t.add(**{**base, **kw})


# ---- location rules --------------------------------------------------------------------------
def test_other_home_cities_follow_the_preference(profile):
    job = "Analyst. 1 year of experience."
    assert fit.check(profile, job, "Mumbai, India").verdict == fit.REVIEW  # default: flag
    strict = with_prefs(profile, other_home_locations="skip")
    mumbai = fit.check(strict, job, "Mumbai, India")
    assert mumbai.verdict == fit.SKIP and "mumbai" in mumbai.reasons[0]
    assert fit.check(strict, job, "Bengaluru, India").verdict == fit.APPLY
    assert fit.check(strict, job, "Remote - India").verdict == fit.APPLY


@pytest.mark.parametrize("location", ["Remote - USA", "Remote - Japan", "Remote - Singapore",
                                      "Bellevue, Washington, USA; Remote - D.C., USA"])
def test_remote_limited_to_another_country_is_skipped(profile, location):
    report = fit.check(profile, "Support engineer. 1 year of experience.", location)
    assert report.verdict == fit.SKIP and "Remote only within" in report.reasons[0]
    assert len(report.reasons) == 1  # no extra "confirm they hire" note on top


@pytest.mark.parametrize("location", ["Remote - Anywhere", "Remote (worldwide)", "Remote"])
def test_open_remote_is_not_skipped(profile, location):
    assert fit.check(profile, "Engineer. 1 year of experience.", location).verdict != fit.SKIP


# ---- pay ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,pay", [("CTC 8-10 LPA", "8-10 LPA"), ("Salary: 12 lakhs per year", "12 LPA"),
                                      ("$85,000 - $95,000 a year", "USD 85,000-95,000"), ("Great team.", "")])
def test_stated_pay(text, pay):
    assert fit.stated_pay(text) == pay


def test_pay_is_saved_and_rescore_applies_new_rules(tmp_path, profile):
    t = Tracker(tmp_path)
    paid = add_job(t)
    assert paid["salary"] == fit.stated_pay(JD) == "6-9 LPA"
    mumbai = add_job(t, location="Mumbai, India")
    mine = add_job(t, location="Mumbai, India", source="manual")
    waiting = add_job(t, location="Mumbai, India")
    t.update(waiting["id"], status="awaiting-human", salary="")
    counts = pipeline.rescore(t, with_prefs(profile, other_home_locations="skip"))
    assert counts["now_skipped"] == 1
    assert t.get(mumbai["id"])["status"] == "skipped"
    assert t.get(mine["id"])["status"] == "discovered" and t.get(mine["id"])["fit"] == "SKIP"  # yours stay
    assert t.get(waiting["id"])["status"] == "awaiting-human" and "mumbai" in t.get(waiting["id"])["notes"]
    assert t.get(paid["id"])["salary"] == "6-9 LPA"


# ---- tailoring REVIEW jobs automatically --------------------------------------------------------------
# Each run re-checks fit from the job text, so these jobs are REVIEW by the rules: another city in the
# home country, which the example profile flags rather than skips.
def build(tracker, prof, job_id, *, offline, say):
    return packet.build_packet(tracker, prof, job_id, offline=True, say=say)


def test_review_jobs_are_tailored_and_handed_over_with_the_reason(tmp_path, profile):
    t = Tracker(tmp_path)
    good = add_job(t)
    elsewhere = add_job(t, location="Pune, India", fit="REVIEW")
    agent.run_once(t, profile, lambda *_: None, discover=lambda: ([], []), build=build, sleep=lambda s: None)
    assert t.get(good["id"])["status"] == "awaiting-human"
    row = t.get(elsewhere["id"])
    assert row["status"] == "awaiting-human" and row["handoff_reason"].startswith("Check first: Located in pune")
    assert row["output_dir"]


def test_review_jobs_are_never_submitted_in_auto_mode(tmp_path, profile):
    t = Tracker(tmp_path)
    add_job(t)
    add_job(t, location="Pune, India", fit="REVIEW")
    t.set_setting("apply_mode", "auto")
    submitted = []

    def fake_apply(tracker, prof, job_id, *, mode, say):
        submitted.append(job_id)
        tracker.update(job_id, status="applied-auto")
        return autoapply.ApplyResult("submitted")
    agent.run_once(t, profile, lambda *_: None, discover=lambda: ([], []), build=build, apply=fake_apply,
                   sleep=lambda s: None)
    assert submitted == ["J0001"]
    assert t.get("J0002")["status"] == "awaiting-human"


def test_setting_can_limit_tailoring_to_apply_jobs(tmp_path, profile):
    t = Tracker(tmp_path)
    add_job(t, location="Pune, India", fit="REVIEW")
    t.set_setting("tailor_fits", "APPLY")
    agent.run_once(t, profile, lambda *_: None, discover=lambda: ([], []), build=build, sleep=lambda s: None)
    assert t.get("J0001")["status"] == "discovered"


# ---- revising a tailored resume on request ---------------------------------------------------------------
def revise_answer(fabricate: bool) -> dict:
    resume = copy.deepcopy(fake_answer(False)["steps4to7_tailored_resume"])
    resume["projects"] = [p for p in resume["projects"] if p["id"] != "made_up_project"]
    resume["experience"].reverse()  # "put my software internship first"
    if fabricate:
        resume["experience"][0]["bullets"][0]["text"] += " Deployed it all on Kubernetes."
    return {"changes_made": ["Moved the Helix Health internship above Northwind"],
            "not_done": ["Did not add Kubernetes: it is not in your profile"],
            "steps4to7_tailored_resume": resume,
            "step8_ats_after": {"overall": 91}, "step9_recruiter_review": {"probability_percent": {"shortlist": 60}},
            "cover_note": ""}


@pytest.fixture
def tailored(tmp_path, profile):
    t = Tracker(tmp_path)
    add_job(t)
    assert packet.build_packet(t, profile, "J0001", say=lambda *_: None, llm=FakeLLM()) == packet.OK
    return t


def test_revise_follows_the_request_rechecks_and_rescores(tailored, profile):
    t = tailored
    said = []
    llm = FakeLLM([revise_answer(True), revise_answer(False)])
    code = packet.revise_packet(t, profile, "J0001", "Put my software internship first, mention Kubernetes",
                                say=said.append, llm=llm)
    assert code == packet.OK
    assert "Put my software internship first" in llm.calls[0] and "CURRENT TAILORED RESUME" in llm.calls[0]
    folder = t.root / t.get("J0001")["output_dir"]
    saved = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in saved["content"]["experience"]] == ["helix", "northwind"]  # the request was followed
    assert "Kubernetes" not in json.dumps(saved["content"])  # the guard still applies
    rev = saved["model_output"]["revisions"][-1]
    assert rev["ats_before"] == "84" and rev["ats_after"] == "91"
    assert t.get("J0001")["ats_after"] == "91"
    assert saved["model_output"]["step1_ats_before"]  # the original analysis is kept
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "Your change requests" in report and "Did not add Kubernetes" in report
    assert (folder / "resume.pdf").exists() and (folder / "resume.docx").exists()
    assert any("not done:  Did not add Kubernetes" in s for s in said) and any("84 -> 91" in s for s in said)


def test_revise_needs_an_instruction_and_a_packet(tmp_path, tailored, profile):
    assert packet.revise_packet(tailored, profile, "J0001", "  ", say=lambda *_: None) == packet.BAD_REQUEST
    t = Tracker(tmp_path / "other")
    add_job(t)
    assert packet.revise_packet(t, profile, "J0001", "shorter", say=lambda *_: None) == packet.BAD_REQUEST


def test_job_page_shows_pay_and_revisions_and_starts_revise(tailored, profile, monkeypatch):
    import shutil

    import ui.app as ui_app
    from tests.conftest import project_ignore
    for folder in ("profile", "config"):
        shutil.copytree(ROOT / folder, tailored.root / folder, ignore=project_ignore())
    packet.revise_packet(tailored, profile, "J0001", "Put my software internship first", say=lambda *_: None,
                         llm=FakeLLM([revise_answer(False)]))
    tailored.update("J0001", salary="9-12 LPA")
    started = []

    class Done:
        stdout = []

        def poll(self):
            return 0
    monkeypatch.setattr(ui_app, "spawn_cli", lambda root, *args: started.append(args) or Done())
    client = ui_app.create_app(tailored.root).test_client()
    page = client.get("/job/J0001").get_data(as_text=True)
    assert "9-12 LPA" in page and "Change this resume" in page and "Put my software internship first" in page
    assert "Did not add Kubernetes" in page
    client.post("/job/J0001/revise", data={"instruction": "Lead with Azure AD"})
    assert started == [("revise", "J0001", "Lead with Azure AD")]
    assert "9-12 LPA" in client.get("/jobs").get_data(as_text=True)
