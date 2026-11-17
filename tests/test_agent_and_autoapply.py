"""SQLite tracker, answer bank, the agent loop, and auto-apply against local practice forms.

The browser tests open a real (hidden) Chromium on forms served from this machine. They are skipped
when Playwright or its browser is not installed. No real job site is contacted.
"""

import csv
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from career import agent, autoapply, packet, pipeline, sources
from career import answers as ans
from career import fit as fitmod
from career.profile import ROOT, Profile
from career.tracker import FIELDS, Tracker
from tests.conftest import project_ignore

JD =(ROOT / "tests" / "sample_jd_iam.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def add_job(t: Tracker, url: str = "https://job-boards.greenhouse.io/acme/jobs/1", **kw) -> dict:
    base = dict(company="Acme", role="IAM Analyst", url=url, location="Bengaluru", text=JD, track="security",
                keyword_score=75, fit="APPLY", source="greenhouse")
    return t.add(**{**base, **kw})


# ---- tracker -------------------------------------------------------------------------------
def test_old_csv_is_imported_once_with_new_status_names(tmp_path):
    (tmp_path / "data").mkdir()
    old_fields = [f for f in FIELDS if f not in ("apply_url", "handoff_reason", "apply_method", "attempts")]
    with open(tmp_path / "data" / "jobs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=old_fields)
        w.writeheader()
        for i, status in enumerate(["found", "analyzed", "applied", "skipped"], start=1):
            w.writerow({"id": f"J{i:04d}", "company": "A", "role": "R", "status": status, "keyword_score": "50"})
    (tmp_path / "data" / "activity.log").write_text("2026-11-15 10:00  J0001 added\n", encoding="utf-8")
    t = Tracker(tmp_path)
    assert [r["status"] for r in t.rows()] == ["discovered", "packet-ready", "completed-manual", "skipped"]
    assert any("J0001 added" in e["message"] for e in t.events())
    assert add_job(t)["id"] == "J0005"
    assert len(Tracker(tmp_path).rows()) == 5  # opening again does not import twice


def test_statuses_notifications_and_settings(tmp_path):
    t = Tracker(tmp_path)
    add_job(t)
    t.update("J0001", status="awaiting-human", handoff_reason="captcha")
    t.notify("needs-you", "captcha", "J0001")
    assert t.get("J0001")["handoff_reason"] == "captcha" and len(t.notifications()) == 1
    row = t.update("J0001", status="applied-auto")
    assert row["date_applied"] and row["handoff_reason"] == "" and t.notifications() == []
    assert t.submitted_today() == 1
    assert t.setting("apply_mode") == "review"
    t.set_setting("apply_mode", "auto")
    assert Tracker(tmp_path).setting("apply_mode") == "auto"
    with pytest.raises(ValueError):
        t.update("J0001", status="nonsense")
    with pytest.raises(ValueError):
        t.update("J0001", not_a_field="x")


# ---- rules -----------------------------------------------------------------------------------
def test_senior_titles_and_headline_years_are_skipped(profile):
    assert fitmod.check(profile, JD, "Bengaluru", "Senior IAM Engineer").verdict == fitmod.SKIP
    assert fitmod.check(profile, JD, "Bengaluru", "IAM Analyst").verdict == fitmod.APPLY
    text = "Security Engineer, Bengaluru. 8+ years of experience in security. 2 years of experience with Terraform."
    assert fitmod.check(profile, text).years_required == 8


def test_discovery_filters_seniority_and_location():
    cfg = {"title_keywords": ["engineer", "analyst"], "exclude_title_keywords": ["senior", "ii"],
           "locations": ["india", "remote"], "ashby": [{"board": "acme", "company": "Acme"}]}
    data = {"jobs": [
        {"title": "Security Engineer", "location": "Bengaluru, India", "jobUrl": "https://jobs.ashbyhq.com/acme/1",
         "applyUrl": "https://jobs.ashbyhq.com/acme/1/application", "descriptionPlain": "x"},
        {"title": "Senior Security Engineer", "location": "Bengaluru, India", "jobUrl": "u2"},
        {"title": "Security Engineer II", "location": "Remote", "jobUrl": "u3"},
        {"title": "Security Engineer", "location": "Berlin, Germany", "jobUrl": "u4"},
        {"title": "Data Analyst", "location": "London", "isRemote": True, "jobUrl": "u5"},
    ]}
    jobs, notes = pipeline.discover(cfg, fetch_json=lambda url: data, delay=0)
    assert [j.url for j in jobs] == ["https://jobs.ashbyhq.com/acme/1", "u5"] and notes == []
    assert jobs[0].extra["apply_url"].endswith("/application")


def test_other_board_parsers():
    [job] = sources.parse_recruitee({"offers": [{"title": "SOC Analyst", "city": "Bengaluru", "country": "India",
                                                 "description": "<p>Monitor</p>", "requirements": "<li>SIEM</li>",
                                                 "careers_url": "https://acme.recruitee.com/o/soc"}]}, "Acme")
    assert job.location == "Bengaluru, India" and "SIEM" in job.description
    [job] = sources.parse_smartrecruiters_list({"content": [{"id": "9", "name": "IAM Engineer", "ref": "https://d/9",
                                                             "location": {"city": "Pune", "country": "in"}}]},
                                               "Acme", "AcmeInc")
    assert job.url == "https://jobs.smartrecruiters.com/AcmeInc/9" and job.extra["detail_url"] == "https://d/9"
    detail = {"jobAd": {"sections": {"jobDescription": {"text": "<p>Azure AD</p>"}}}}
    assert sources.smartrecruiters_detail(detail) == "Azure AD"
    [job] = sources.parse_greenhouse({"jobs": [{"id": 77, "title": "T", "absolute_url": "https://acme.com/j?gh_jid=77",
                                                "content": "x"}]}, "Acme", "acme")
    assert job.extra["apply_url"] == "https://job-boards.greenhouse.io/acme/jobs/77"
    [job] = sources.parse_remotive({"jobs": [{"title": "Dev", "company_name": "R", "url": "https://remotive.com/1",
                                              "candidate_required_location": "India", "description": "<b>x</b>"}]})
    assert job.location == "Remote (India)"


# ---- answer bank -----------------------------------------------------------------------------
def test_answers_come_only_from_profile_and_your_file(profile):
    row = {"source": "greenhouse"}
    fit = fitmod.check(profile, JD, "Bengaluru")
    rules = ans.build(profile, row, fit, extra={"answers": [
        {"question": "current (ctc|salary)", "answer": "Internship stipend"}, {"question": "gender", "answer": ""}]})
    get = lambda label: getattr(ans.answer_for(rules, label), "value", None)  # noqa: E731
    assert get("First Name *") == "Alex" and get("Last Name") == "Morgan" and get("Name") == "Alex Morgan"
    assert get("Email") == profile.contact["email"] and get("LinkedIn Profile") == profile.contact["linkedin"]
    assert get("Will you now or in the future require sponsorship?") == "No"
    assert get("Are you legally authorized to work in India?") == "Yes"
    assert get("What is your current CTC?") == "Internship stipend"
    assert get("Describe your experience with Kubernetes in production") is None
    assert get("Gender") is None and get("Why do you want to work here?") is None  # no cover note: no answer
    abroad = fitmod.check(profile, "Engineer in Berlin, Germany. Visa sponsorship available.", "Berlin")
    rules = ans.build(profile, row, abroad, extra={})
    assert ans.answer_for(rules, "Do you require visa sponsorship?").value == "Yes"


def test_pick_option_never_takes_a_near_miss():
    assert ans.pick_option(["Yes", "No"], "No") == 1
    assert ans.pick_option(["Yes, I am authorized", "No, I am not"], "Yes") == 0
    assert ans.pick_option(["Bengaluru, Karnataka, India", "Chennai, India"], "Bengaluru") == 0
    assert ans.pick_option(["Male", "Female", "Decline"], "Bengaluru") is None
    assert ans.pick_option(["Bengaluru Urban", "Bengaluru Rural"], "Bengaluru") is None  # two candidates: no guess


def test_account_sites_are_never_automated(tmp_path, profile):
    assert autoapply.host_kind("https://www.linkedin.com/jobs/view/1/") == "blocked"
    assert autoapply.host_kind("https://www.naukri.com/job-listings-x") == "blocked"
    assert autoapply.host_kind("https://job-boards.greenhouse.io/acme/jobs/1") == "supported"
    assert autoapply.host_kind("https://jobs.lever.co/acme/1/apply") == "supported"
    assert autoapply.host_kind("https://careers.example.com/apply") == "unsupported"
    assert autoapply.host_kind("https://evilgreenhouse.io/x") == "unsupported"
    t = Tracker(tmp_path)
    add_job(t, url="https://www.linkedin.com/jobs/view/1/", source="linkedin-alert")
    packet.build_packet(t, profile, "J0001", offline=True, say=lambda *_: None)
    for mode in ("auto", "takeover"):  # no browser is started for these: it would raise if Playwright were missing
        result = autoapply.apply_to_job(t, profile, "J0001", mode=mode, say=lambda *_: None)
        assert result.outcome == "manual-only"
    row = t.get("J0001")
    assert row["status"] == "awaiting-human" and "never automated" in row["handoff_reason"]


# ---- agent loop (browser and Gemini replaced by stand-ins) ---------------------------------------
def test_agent_run_tailors_applies_and_hands_off(tmp_path, profile):
    t = Tracker(tmp_path)
    jobs = [sources.Job("greenhouse", "IAM Analyst", "Acme", "https://job-boards.greenhouse.io/acme/jobs/1",
                        "Bengaluru", JD),
            sources.Job("greenhouse", "Identity Engineer", "Beta", "https://job-boards.greenhouse.io/beta/jobs/2",
                        "Bengaluru", JD),
            sources.Job("greenhouse", "Senior IAM Architect", "Gamma", "https://job-boards.greenhouse.io/g/jobs/3",
                        "Bengaluru", JD),
            sources.Job("linkedin-alert", "SOC Analyst", "", "https://www.linkedin.com/jobs/view/9/")]
    said, applied = [], []

    def build(tracker, prof, job_id, *, offline, say):
        return packet.build_packet(tracker, prof, job_id, offline=True, say=say)

    def fake_apply(tracker, prof, job_id, *, mode, say):
        applied.append(job_id)
        if job_id == "J0001":
            tracker.update(job_id, status="applied-auto")
            return autoapply.ApplyResult("submitted")
        tracker.update(job_id, status="awaiting-human", handoff_reason="The form shows a captcha.")
        return autoapply.ApplyResult("handoff", "captcha")

    # review mode (the default): nothing is submitted
    stats = agent.run_once(t, profile, said.append, discover=lambda: (jobs, []), build=build, apply=fake_apply,
                           sleep=lambda s: None)
    assert stats == {"found": 2, "packets": 2, "submitted": 0, "needs_you": 2} and applied == []
    by_role = {r["role"]: r for r in t.rows()}
    assert by_role["Senior IAM Architect"]["status"] == "skipped"
    assert by_role["SOC Analyst"]["status"] == "needs-description"
    assert by_role["IAM Analyst"]["status"] == "awaiting-human"

    # auto mode: one submitted, one handed off, daily limit respected
    for job_id in ("J0001", "J0002"):
        t.update(job_id, status="packet-ready")
    t.set_setting("apply_mode", "auto")
    stats = agent.run_once(t, profile, said.append, discover=lambda: ([], []), build=build, apply=fake_apply,
                           sleep=lambda s: None)
    assert stats["submitted"] == 1 and stats["needs_you"] == 1 and applied == ["J0001", "J0002"]
    t.update("J0002", status="packet-ready")
    t.set_setting("max_applications_per_day", "1")
    agent.run_once(t, profile, said.append, discover=lambda: ([], []), build=build, apply=fake_apply,
                   sleep=lambda s: None)
    assert applied == ["J0001", "J0002"] and any("daily application limit" in line for line in said)


def test_add_manual_scores_reuses_links_and_rejects_bad_input(tmp_path, profile):
    t = Tracker(tmp_path)
    row, new = pipeline.add_manual(t, profile, url="https://jobs.lever.co/acme/1", text=JD, company="Acme",
                                   role="IAM Analyst", location="Bengaluru")
    assert new and row["source"] == "manual" and int(row["keyword_score"]) > 0 and row["fit"]
    again, new = pipeline.add_manual(t, profile, url="https://jobs.lever.co/acme/1/", text=JD, company="X", role="Y")
    assert not new and again["id"] == row["id"]
    with pytest.raises(ValueError, match="too short"):
        pipeline.add_manual(t, profile, url="", text="short", company="A", role="B")


def test_apply_now_follows_apply_mode(tmp_path, profile):
    t = Tracker(tmp_path)
    built, applied = [], []

    def build(tracker, prof, job_id, *, offline, say):
        built.append(job_id)
        return packet.build_packet(tracker, prof, job_id, offline=True, say=say)

    def fake_apply(tracker, prof, job_id, *, mode, say):
        applied.append((job_id, mode))
        return autoapply.ApplyResult("handoff" if mode == "takeover" else "submitted")

    def go(job_id):
        return agent.apply_now(t, profile, job_id, lambda *_: None, build=build, apply=fake_apply)

    add_job(t)
    assert go("J0001") == "handoff" and applied == [("J0001", "takeover")]  # review: you press Submit
    assert built == ["J0001"]
    go("J0001")
    assert built == ["J0001"]  # an existing packet is reused

    t.set_setting("apply_mode", "off")
    assert go("J0001") == "packet-ready" and len(applied) == 2

    t.set_setting("apply_mode", "auto")
    assert go("J0001") == "submitted" and applied[-1] == ("J0001", "auto")
    add_job(t, url="https://job-boards.greenhouse.io/acme/jobs/2", fit="SKIP")
    assert go("J0002") == "handoff" and t.get("J0002")["status"] == "awaiting-human"  # never auto-submits a SKIP
    assert applied[-1][0] == "J0001"
    t.set_setting("max_applications_per_day", "0")
    assert go("J0001") == "handoff" and "limit" in t.get("J0001")["handoff_reason"]

    t.update("J0001", status="applied-auto")
    assert go("J0001") == "already-submitted"

    def failing(tracker, prof, job_id, *, offline, say):
        return packet.LLM_FAILED
    add_job(t, url="https://job-boards.greenhouse.io/acme/jobs/3")
    assert agent.apply_now(t, profile, "J0003", lambda *_: None, build=failing, apply=fake_apply) == "no-packet"


def test_agent_stops_tailoring_when_gemini_fails_and_skips_low_scores(tmp_path, profile):
    t = Tracker(tmp_path)
    add_job(t)
    add_job(t, url="https://job-boards.greenhouse.io/acme/jobs/2")
    add_job(t, url="https://job-boards.greenhouse.io/acme/jobs/3", keyword_score=20)
    add_job(t, url="https://job-boards.greenhouse.io/acme/jobs/4", fit="REVIEW")
    calls = []

    def build(tracker, prof, job_id, *, offline, say):
        calls.append(job_id)
        return packet.LLM_FAILED

    agent.run_once(t, profile, lambda *_: None, discover=lambda: ([], []), build=build, sleep=lambda s: None)
    assert calls == ["J0001"]  # stopped after the first failure; low score and REVIEW never attempted
    assert any(n["kind"] == "gemini" for n in t.notifications())


# ---- real browser against practice forms on this machine ---------------------------------------------
FORM = """<!doctype html><title>Apply</title><h1>Apply for IAM Analyst</h1>
<form id="f">
<label for="first_name">First Name *</label><input id="first_name" name="first_name" required>
<label for="last_name">Last Name *</label><input id="last_name" name="last_name" required>
<label for="email">Email *</label><input id="email" name="email" type="email" required>
<label for="phone">Phone</label><input id="phone" name="phone" type="tel">
<label for="resume">Resume/CV *</label><button type="button">Attach</button>
<input id="resume" name="resume" type="file" style="display:none" required>
<label id="loc-l">Location (City) *</label>
<input id="loc" name="loc" role="combobox" aria-labelledby="loc-l" autocomplete="off"><div id="opts"></div>
<label for="li">LinkedIn Profile</label><input id="li" name="linkedin">
<label for="auth">Are you legally authorized to work in India? *</label>
<select id="auth" name="auth" required><option value="">Select...</option><option value="1">Yes</option><option value="0">No</option></select>
<fieldset><legend>Will you now or in the future require sponsorship? *</legend>
<label><input type="radio" name="spons" value="y" required> Yes</label>
<label><input type="radio" name="spons" value="n"> No</label></fieldset>
<label for="gender">Gender</label><select id="gender" name="gender"><option value="">Select...</option><option>Female</option><option>Male</option><option>Decline</option></select>
<input type="text" name="trap" style="display:none">
EXTRA
<button type="submit">Submit application</button></form>
<script>
const loc = document.getElementById('loc'), opts = document.getElementById('opts');
loc.addEventListener('input', () => { opts.innerHTML = '';
  for (const c of ['Bengaluru, Karnataka, India', 'Chennai, Tamil Nadu, India']) if (c.toLowerCase().startsWith(loc.value.toLowerCase()) && loc.value) {
    const d = document.createElement('div'); d.setAttribute('role', 'option'); d.textContent = c;
    d.onclick = () => { loc.value = c; opts.innerHTML = ''; }; opts.appendChild(d); } });
document.getElementById('f').addEventListener('submit', async e => { e.preventDefault();
  const data = {}; for (const [k, v] of new FormData(e.target)) data[k] = v instanceof File ? v.name : v;
  await fetch('/record', {method: 'POST', body: JSON.stringify(data)});
  document.body.innerHTML = '<h1>Thank you for applying</h1><p>Your application has been received.</p>'; });
if (location.search.includes('auto')) setTimeout(() => { for (const el of document.querySelectorAll('[required]')) el.required = false;
  document.getElementById('f').requestSubmit(); }, 1500);
</script>"""
PAGES = {
    "/good": FORM.replace("EXTRA", ""),
    "/question": FORM.replace("EXTRA", '<label for="k8s">Describe your experience running Kubernetes in production *'
                                       '</label><textarea id="k8s" name="k8s" required></textarea>'),
    "/captcha": FORM.replace("EXTRA", '<iframe src="/recaptcha/api2/bframe" width="300" height="400"></iframe>'),
    "/consent": FORM.replace("EXTRA", '<label><input type="checkbox" name="ok" required> I agree to the privacy '
                                      'policy *</label>'),
    "/split": FORM.replace("EXTRA", """
<label for="cc">Phone country code *</label><select id="cc" name="cc" required><option value="">Select...</option>
<option>United States (+1)</option><option>India (+91)</option></select>
<label for="city">City *</label><input id="city" name="city" required>
<label for="ctry">Country *</label><select id="ctry" name="ctry" required><option value="">Select...</option>
<option>Indonesia</option><option>India</option></select>"""),
    # Like Greenhouse: a hidden phone-code list that also uses role=option, a dropdown that opens on click with
    # a hidden twin input for validation, a tick-the-source question, and a "source of right to work" question.
    "/dropdowns": FORM.replace("EXTRA", """
<div style="display:none"><div role="option">Afghanistan +93</div><div role="option">India +91</div></div>
<label id="c-l">Country *</label><input id="country" role="combobox" aria-labelledby="c-l" autocomplete="off">
<input id="country-twin" aria-hidden="true" tabindex="-1" required style="opacity:0;width:1px">
<input type="hidden" name="country"><div id="copts"></div>
<fieldset><legend>How did you hear about us? *</legend>
<label><input type="checkbox" name="heard" value="site"> Careers Website</label>
<label><input type="checkbox" name="heard" value="li"> LinkedIn</label>
<label><input type="checkbox" name="heard" value="gd"> Glassdoor</label></fieldset>
<label for="rtw">What is the source of your right to work where this role is listed? *</label>
<select id="rtw" name="rtw" required><option value="">Select...</option><option>Citizen</option><option>Visa</option></select>
<script>
const cb = document.getElementById('country'), copts = document.getElementById('copts');
cb.addEventListener('click', () => { copts.innerHTML = '';
  for (const c of ['India', 'Indonesia']) { const d = document.createElement('div'); d.setAttribute('role', 'option');
    d.textContent = c; d.onclick = () => { cb.value = c; document.getElementById('country-twin').value = c;
      document.querySelector('[name=country]').value = c; copts.innerHTML = ''; }; copts.appendChild(d); } });
</script>"""),
}


@pytest.fixture(scope="module")
def site():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path.split("?")[0], "<p>frame</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


@pytest.fixture
def browser_job(tmp_path, profile, site, monkeypatch):
    try:
        pw, browser = autoapply._launch(True)
    except autoapply.Unavailable as exc:
        pytest.skip(str(exc))
    browser.close()
    pw.stop()
    monkeypatch.setitem(os.environ, "CAREER_AGENT_HEADLESS", "1")
    t = Tracker(tmp_path)
    t.set_setting("handoff_wait_minutes", "1")
    site[1].clear()

    def make(path: str) -> str:
        row = add_job(t, url=site[0] + path)
        packet.build_packet(t, profile, row["id"], offline=True, say=lambda *_: None)
        return row["id"]

    def go(job_id: str, mode: str = "auto"):
        return autoapply.apply_to_job(t, profile, job_id, mode=mode, supported=("127.0.0.1",), say=lambda *_: None)
    return t, make, go, site[1]


def test_browser_fills_and_submits_a_complete_form(browser_job, profile):
    t, make, go, received = browser_job
    job_id = make("/good")
    result = go(job_id)
    assert result.outcome == "submitted" and result.unresolved == []
    [data] = received
    assert data["first_name"] == "Alex" and data["last_name"] == "Morgan" and data["email"] == profile.contact["email"]
    assert data["resume"] == "Alex_Morgan_Resume.pdf" and data["loc"] == "Bengaluru, Karnataka, India"
    assert data["auth"] == "1" and data["spons"] == "n" and data["linkedin"] == profile.contact["linkedin"]
    assert data["gender"] == "" and data["trap"] == ""  # optional diversity question and hidden field left alone
    row = t.get(job_id)
    assert row["status"] == "applied-auto" and row["date_applied"]
    assert (t.root / row["output_dir"] / "submitted.png").exists()


@pytest.mark.parametrize("path, expect", [("/question", "Kubernetes"), ("/captcha", "captcha"),
                                          ("/consent", "privacy")])
def test_browser_hands_off_instead_of_guessing(browser_job, path, expect):
    t, make, go, received = browser_job
    job_id = make(path)
    result = go(job_id)
    row = t.get(job_id)
    assert result.outcome == "handoff" and received == []  # nothing was submitted
    assert row["status"] == "awaiting-human" and expect.lower() in row["handoff_reason"].lower()
    assert any(n["job_id"] == job_id for n in t.notifications())


def test_dropdowns_and_source_question_are_answered_without_guessing(browser_job):
    t, make, go, received = browser_job
    result = go(make("/dropdowns"))
    assert "Country *" in result.filled and "How did you hear about us? *" in result.filled
    # The hidden validation twin is not a question of its own; "source of your right to work" is not a Yes/No,
    # so it is the only thing left for you.
    assert result.outcome == "handoff" and received == []
    assert result.unresolved == ["What is the source of your right to work where this role is listed? *"]


def test_country_code_city_and_country_boxes_are_filled_from_the_profile(browser_job, profile):
    t, make, go, received = browser_job
    assert go(make("/split")).outcome == "submitted"
    [data] = received
    assert data["cc"] == "India (+91)" and data["phone"] == profile.contact["phone"].removeprefix("+91").strip()
    assert data["city"] == profile.contact["location"].split(",")[0] and data["ctry"] == "India"


def test_city_has_its_other_name_for_dropdowns(profile):
    rules = ans.build(profile, {"source": ""}, fitmod.check(profile, "", "Bengaluru", "Analyst"), extra={})
    city = ans.answer_for(rules, "City")
    assert city.key == "city" and city.alts == ("Bangalore",)
    assert ans.pick_answer(["Bangalore, Karnataka", "Delhi"], city) == 0
    assert ans.answer_for(rules, "Phone country code").value == "+91"


def test_consent_box_is_ticked_only_when_you_allow_it(browser_job):
    t, make, go, received = browser_job
    t.set_setting("tick_consent_boxes", "on")
    assert go(make("/consent")).outcome == "submitted" and received[0]["ok"] == "on"


def test_takeover_marks_completed_when_you_submit(browser_job):
    t, make, go, received = browser_job
    job_id = make("/question?auto=1")  # the page submits itself after 1.5 s, standing in for you pressing Submit
    result = go(job_id, mode="takeover")
    assert result.outcome == "completed-manual" and "Kubernetes" in " ".join(result.unresolved)
    assert t.get(job_id)["status"] == "completed-manual"


# ---- dashboard additions -------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, profile, monkeypatch):
    import shutil

    import ui.app as ui_app
    for folder in ("profile", "config"):
        shutil.copytree(ROOT / folder, tmp_path / folder, ignore=project_ignore())
    t = Tracker(tmp_path)
    add_job(t)
    packet.build_packet(t, profile, "J0001", offline=True, say=lambda *_: None)
    t.update("J0001", status="awaiting-human", handoff_reason="The form shows a captcha.")
    add_job(t, url="https://www.naukri.com/job-listings-1", source="naukri-alert")
    t.update("J0002", status="awaiting-human", handoff_reason="never automated")
    started = []

    class Done:
        stdout = []

        def poll(self):
            return 0
    monkeypatch.setattr(ui_app, "spawn_cli", lambda root, *args: started.append(args) or Done())
    app = ui_app.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client(), t, started


def test_needs_you_page_and_actions(client):
    c, t, started = client
    page = c.get("/needs").get_data(as_text=True)
    assert "The form shows a captcha." in page and "<button>Take over</button>" in page and "Open listing" in page
    assert "/job/J0001#change" in page  # straight to "Change this resume"
    assert 'class="badge">2<' in c.get("/").get_data(as_text=True)
    c.post("/job/J0001/takeover")
    c.post("/run")
    assert started == [("takeover", "J0001"), ("run",)]


def test_apply_result_is_shown_even_when_it_fails(client, monkeypatch):
    import ui.app as ui_app
    c, t, started = client

    class Failed:  # e.g. Gemini was over its limit: the reason must reach you, not vanish
        stdout = ["Gemini error: free-tier limit reached\n", "J0001: no-packet\n"]

        def poll(self):
            return 2
    monkeypatch.setattr(ui_app, "spawn_cli", lambda root, *args: Failed())
    c.post("/job/J0001/apply-now")
    import time
    time.sleep(0.2)  # the output reader runs in a thread
    page = c.get("/job/J0001").get_data(as_text=True)
    assert "Apply J0001" in page and "free-tier limit reached" in page
    assert "free-tier limit reached" not in c.get("/job/J0001").get_data(as_text=True)  # shown once


def test_open_takeover_browser_does_not_block_the_agent(client, monkeypatch):
    import ui.app as ui_app
    c, t, started = client

    class Waiting:  # a take-over browser still open, waiting for you to press Submit
        stdout = []

        def poll(self):
            return None
    monkeypatch.setattr(ui_app, "spawn_cli", lambda root, *args: started.append(args) or Waiting())
    c.post("/job/J0001/takeover")
    page = c.post("/job/J0001/takeover", follow_redirects=True).get_data(as_text=True)
    assert "already open" in page  # a second click does not open a second browser
    c.post("/run")  # the agent still runs while the browser waits
    assert started == [("takeover", "J0001"), ("run",)]
    c.post("/job/J0001/status", data={"status": "completed-manual", "back": "needs"})
    assert t.get("J0001")["status"] == "completed-manual" and t.get("J0001")["date_applied"]
    left = c.get("/needs").get_data(as_text=True)  # only the Naukri job is left: a link, no automation button
    assert "<button>Take over</button>" not in left and "Open listing" in left


def test_add_job_page_saves_then_applies_in_background(client):
    c, t, started = client
    page = c.get("/add").get_data(as_text=True)
    assert "Tailor resume and apply" in page and "press Submit" in page  # review mode is described
    form = {"url": "https://jobs.lever.co/acme/7", "company": "Acme", "role": "IAM Analyst",
            "location": "Bengaluru", "text": JD}
    resp = c.post("/add", data=form)
    new_id = resp.headers["Location"].rsplit("/", 1)[-1]
    assert t.get(new_id)["company"] == "Acme" and started == [("apply-now", new_id)]

    c.post("/add", data={**form, "url": "https://jobs.lever.co/acme/8", "only_save": "1"})
    assert len(started) == 1  # only saved
    bad = c.post("/add", data={**form, "url": "https://jobs.lever.co/acme/9", "text": "too short"})
    assert "too short" in bad.get_data(as_text=True)
    bad = c.post("/add", data={**form, "url": "javascript:alert(1)"})
    assert "must start with https://" in bad.get_data(as_text=True)

    job = c.get("/job/J0001").get_data(as_text=True)
    assert "Tailored resume" in job and "iframe" in job and "Apply now" in job
    c.post("/job/J0001/apply-now")
    assert started[-1] == ("apply-now", "J0001")


def test_apply_in_own_browser_links_to_the_form_and_shows_answers(client, monkeypatch):
    c, t, started = client
    form = "https://job-boards.greenhouse.io/acme/jobs/1"
    needs = c.get("/needs").get_data(as_text=True)
    assert f'href="{form}" target="_blank"' in needs and "Apply in my browser" in needs  # a real link
    page = c.get("/job/J0001").get_data(as_text=True)
    assert "Your answers" in page and "Open the application form" in page and f'data-v="{form}"' in page
    assert "Portfolio / website" in page and "Notice period" in page and 'class="ghost copy"' in page

    opened = []
    monkeypatch.setattr(autoapply, "_open_folder", lambda folder: opened.append(folder.name))
    resp = c.post("/job/J0001/open-folder")
    assert resp.headers["Location"].endswith("/job/J0001#answers") and not started  # no agent browser
    assert opened[0].startswith("J0001_") and t.get("J0001")["apply_method"] == "own-browser"


def test_jobs_page_search_and_applied_tab(client):
    c, t, _ = client
    add_job(t, url="https://jobs.lever.co/zeta/5", company="Zeta", role="Cloud Analyst")
    t.update("J0003", status="discovered")
    t.update("J0001", status="completed-manual", date_applied="2026-11-16", salary="9-12 LPA")
    open_tab = c.get("/jobs").get_data(as_text=True)
    assert "Cloud Analyst" in open_tab and "2026-11-16" not in open_tab and 'type="search"' in open_tab
    applied = c.get("/jobs?tab=applied").get_data(as_text=True)
    assert "2026-11-16" in applied and "9-12 LPA" in applied and "Report" in applied and "Cloud Analyst" not in applied
    found = c.get("/jobs?q=zeta+cloud").get_data(as_text=True)
    assert "Cloud Analyst" in found and "J0002" not in found
    c.post("/job/J0001/status", data={"status": "interview", "back": "applied"})
    assert t.get("J0001")["status"] == "interview"
    assert "interview" in c.get("/jobs?tab=applied").get_data(as_text=True)


def test_job_page_at_a_glance_and_needs_summary(client):
    c, t, _ = client
    page = c.get("/job/J0001").get_data(as_text=True)
    assert "At a glance" in page and "Experience asked" in page and "You have:" in page
    assert "<details><summary><b>Full job description</b>" in page  # the long text is folded away
    assert "Experience:" in c.get("/needs").get_data(as_text=True)


def test_bookmark_prefills_add_job_without_saving(client):
    c, t, started = client
    before = len(t.rows())
    page = c.get("/add?url=https://www.linkedin.com/jobs/view/42/&role=SOC+Analyst&company=Acme&text=Monitor+alerts"
                 ).get_data(as_text=True)
    assert 'value="https://www.linkedin.com/jobs/view/42/"' in page and 'value="SOC Analyst"' in page
    assert "Monitor alerts</textarea>" in page and "Save to Career Agent" in page
    assert len(t.rows()) == before and not started  # a GET only fills the form in


def test_question_labels_are_readable():
    from ui.app import _question_label
    assert _question_label("current (ctc|salary|compensation)") == "Current ctc / salary / compensation"


def test_settings_and_logs_pages(client):
    c, t, _ = client
    c.post("/settings", data={"apply_mode": "auto", "min_keyword_score": "60", "max_packets_per_run": "abc",
                              "use_gemini": "maybe"})
    s = t.settings()
    assert s["apply_mode"] == "auto" and s["min_keyword_score"] == "60"
    assert s["max_packets_per_run"] == "8" and s["use_gemini"] == "on"  # bad values are ignored
    bad = c.post("/settings/file/answers", data={"text": "answers: [unclosed"}, follow_redirects=True)
    assert "Not saved" in bad.get_data(as_text=True)
    c.post("/settings/file/answers", data={"text": "portfolio: https://example.com\n"})
    assert "example.com" in (t.root / "config" / "answers.yaml").read_text()
    assert c.post("/settings/file/secrets", data={"text": "x"}).status_code == 404
    logs = c.get("/activity").get_data(as_text=True)
    assert "settings saved" in logs and "J0001" in logs


def test_dashboard_refuses_requests_from_other_sites(client):
    c, t, started = client
    assert c.post("/run", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert c.post("/run", headers={"Origin": "http://localhost"}).status_code == 302
    assert started == [("run",)]
