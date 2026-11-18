"""Truthfulness guard, Gemini pipeline (with a fake model), renderers and the CLI."""

import copy
import shutil
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

from career import analyze, guard
from career.fit import check as fit_check
from career.llm import GeminiLLM, LLMError, parse_json
from career.profile import ROOT, Profile
from career.render import write_docx, write_pdf
from career.resume import base_content, layout
from tests.conftest import project_ignore
from tests.fakes import FakeLLM, fake_answer

JD = (ROOT / "tests" / "sample_jd_iam.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


# ---- bullet guard -------------------------------------------------------------------
@pytest.mark.parametrize("entry,source,text", [
    ("northwind", "northwind.access", "Administered IAM for 500+ users across 15+ enterprise applications in Azure AD, "
                          "reducing access incidents by 25%."),
    ("iac", "iac.pipeline", "Built a DevSecOps CI/CD pipeline in Python and Terraform that auto-remediates drift "
                            "across 10 AWS resources."),
    ("helix", "helix.prs", "Shipped changes through Git pull requests that senior engineers reviewed."),
])
def test_honest_rewrites_pass(profile, entry, source, text):
    assert guard.check_bullet(profile, entry, source, text) == []


@pytest.mark.parametrize("entry,source,text,reason", [
    ("northwind", "northwind.access", "Administered IAM for 800+ users across 15+ applications.", "numbers"),
    ("northwind", "northwind.access", "Administered IAM for 500+ users across 15+ applications using Okta.", "Okta"),
    ("northwind", "northwind.tickets", "Led a team resolving 70+ monthly tickets at 98% SLA compliance.", "seniority"),
    ("northwind", "northwind.tickets", "Resolved 70+ monthly tickets and triaged Splunk SIEM alerts at 98% SLA.",
     "SIEM"),
    ("iac", "iac.pipeline", "Built a Docker and Kubernetes pipeline remediating drift in 10 AWS resources.", "Docker"),
    ("helix", "northwind.access", "A bullet borrowed from another job, long enough to pass length.", "unknown source"),
    ("northwind", "northwind.access", "Short.", "length"),
])
def test_fabrications_are_caught(profile, entry, source, text, reason):
    problems = " ".join(guard.check_bullet(profile, entry, source, text))
    assert reason in problems


def test_sanitize_reverts_restores_and_filters(profile):
    proposed = fake_answer(fabricate=True)["steps4to7_tailored_resume"]
    result = guard.sanitize(profile, "security", proposed)
    content = result.content
    assert content["headline"] == profile.tracks["security"]["headline"]  # Okta headline rejected
    northwind = next(e for e in content["experience"] if e["id"] == "northwind")
    access = next(b for b in northwind["bullets"] if b["source_id"] == "northwind.access")
    assert access["text"] == profile.bullet_text("northwind.access", "security")  # reverted
    assert [p["id"] for p in content["projects"]] == ["iac", "malware", "scanner"]  # fake dropped, rest restored
    all_skills = [s for names in content["skills"].values() for s in names]
    assert "Okta" not in all_skills
    assert content["skills"]["familiar"] == ["SIEM"]
    assert result.reverted >= 2


def test_sanitize_with_nothing_gives_base(profile):
    result = guard.sanitize(profile, "software", {})
    base = base_content(profile, "software")
    assert result.content["experience"] == base["experience"]
    assert result.content["skills"] == base["skills"]


def test_every_number_on_a_tailored_resume_exists_in_profile(profile):
    result = analyze.run(profile, FakeLLM([fake_answer(True)] * 2), JD)
    from career.resume import plain_text
    text = plain_text(result.doc)
    allowed = guard._numbers(Path(ROOT / "profile" / "baseline.example.yaml").read_text())
    assert guard._numbers(text) <= allowed


# ---- pipeline ------------------------------------------------------------------------
def test_run_uses_repair_pass_and_keeps_clean_rewrites(profile):
    llm = FakeLLM()
    result = analyze.run(profile, llm, JD)
    assert len(llm.calls) == 2 and result.iterations == 2
    assert result.track == "security"
    assert result.content["headline"].startswith("IAM Analyst")  # kept (show_headline only hides it on the page)
    assert result.rewrites_kept >= 3
    assert "Northwind" in result.model_output["cover_note"]
    assert "PREVIOUS ANSWER" in llm.calls[1]


def test_clean_first_answer_needs_one_call(profile):
    clean = copy.deepcopy(fake_answer(False))
    clean["steps4to7_tailored_resume"]["projects"] = [
        p for p in clean["steps4to7_tailored_resume"]["projects"] if p["id"] != "made_up_project"]
    clean["steps4to7_tailored_resume"]["skills"]["security"].remove("Okta")
    llm = FakeLLM([clean])
    analyze.run(profile, llm, JD)
    assert len(llm.calls) == 1


def test_prompt_contains_rules_facts_and_job(profile):
    from career.keywords import match
    prompt = analyze.build_prompt(profile, "security", JD, match(profile, JD))
    assert "northwind.access" in prompt and "IAM Analyst" in prompt and "step9_recruiter_review" in prompt
    assert "Never invent" in analyze.SYSTEM


def test_report_has_all_nine_steps(profile):
    result = analyze.run(profile, FakeLLM(), JD)
    report = analyze.report_markdown({"id": "J0001", "role": "IAM Analyst", "company": "Acme"}, result,
                                     fit_check(profile, JD))
    for marker in ["Step 1 and Step 8", "Step 2", "Step 3", "Steps 4-7", "Step 9", "Truthfulness guard log",
                   "Learn next"]:
        assert marker in report


# ---- llm helpers -------------------------------------------------------------------------
def test_parse_json_handles_fences_and_noise():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_missing_key_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "paste-your-key-here")
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiLLM(api_key=None)


def test_network_failure_becomes_friendly_error(monkeypatch):
    llm = GeminiLLM(api_key="test-key", models=["m1"], sleep=lambda s: None)

    class Down:
        def generate_content(self, **kwargs):
            raise ConnectionError("proxy said no")
    monkeypatch.setattr(llm, "_client", type("C", (), {"models": Down()})())
    with pytest.raises(LLMError, match="internet connection"):
        llm.generate_json("s", "p")


def test_rate_limit_falls_back_to_next_model(monkeypatch):
    from google.genai import errors
    llm = GeminiLLM(api_key="test-key", models=["busy", "ok"], sleep=lambda s: None)
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            if model == "busy":
                body = {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
                raise errors.ClientError(429, body)
            return type("R", (), {"text": '{"ok": true}'})()
    monkeypatch.setattr(llm, "_client", type("C", (), {"models": Models()})())
    assert llm.generate_json("s", "p") == {"ok": True}
    assert calls == ["busy", "busy", "ok"] and llm.last_model == "ok"


# ---- renderers ------------------------------------------------------------------------------
@pytest.mark.parametrize("track", ["security", "software"])
def test_base_resumes_render_one_page_with_links(profile, tmp_path, track):
    doc = layout(profile, base_content(profile, track))
    assert write_pdf(doc, tmp_path / "r.pdf") == 1
    reader = PdfReader(tmp_path / "r.pdf")
    links = [a.get_object()["/A"]["/URI"] for a in reader.pages[0]["/Annots"]]
    assert "https://linkedin.com/in/example-alex-morgan" in links and "https://github.com/example-alex-morgan" in links
    text = reader.pages[0].extract_text()
    for heading in ["Experience", "Projects", "Skills", "Education"]:
        assert heading in text
    write_docx(doc, tmp_path / "r.docx")
    from docx import Document
    body = "\n".join(p.text for p in Document(tmp_path / "r.docx").paragraphs)
    assert "ALEX MORGAN" in body and "Experience" in body


def test_header_shows_portfolio_only_when_set(profile, tmp_path):
    from docx import Document
    doc = layout(profile, base_content(profile, "security"))
    assert write_pdf(doc, tmp_path / "r.pdf") == 1
    reader = PdfReader(tmp_path / "r.pdf")
    links = [a.get_object()["/A"]["/URI"] for a in reader.pages[0]["/Annots"]]
    assert "https://example-alex-morgan.github.io" in links  # clickable
    write_docx(doc, tmp_path / "r.docx")
    lines = [p.text for p in Document(tmp_path / "r.docx").paragraphs][:5]
    assert any(line.startswith("linkedin.com/in/example-alex-morgan") and line.endswith("example-alex-morgan.github.io")
               for line in lines)  # links on their own line, same as the PDF

    no_site = dict(doc, contact={k: v for k, v in doc["contact"].items() if k != "portfolio"})
    assert write_pdf(no_site, tmp_path / "n.pdf") == 1
    links = [a.get_object()["/A"]["/URI"] for a in PdfReader(tmp_path / "n.pdf").pages[0]["/Annots"]]
    assert not any("github.io" in u for u in links)
    write_docx(no_site, tmp_path / "n.docx")
    lines = [p.text for p in Document(tmp_path / "n.docx").paragraphs][:5]
    assert any("@example.com" in line and "github.com/example-alex-morgan" in line for line in lines)  # one line


def test_international_drops_school(profile):
    doc = layout(profile, base_content(profile, "security"), international=True)
    assert [e["school"] for e in doc["education"]] == ["Example Institute of Technology, Bengaluru"]


# ---- CLI end to end (on a copy of the project) ----------------------------------------------------
@pytest.fixture
def project(tmp_path, monkeypatch):
    dst = tmp_path / "agent"
    shutil.copytree(ROOT, dst, ignore=project_ignore())
    for mod in [m for m in sys.modules if m == "jobs" or m.startswith("career")]:
        del sys.modules[mod]
    monkeypatch.syspath_prepend(str(dst))
    monkeypatch.chdir(dst)
    yield dst
    for mod in [m for m in sys.modules if m == "jobs" or m.startswith("career")]:
        del sys.modules[mod]


def test_cli_full_flow(project, monkeypatch, capsys):
    import career.llm
    import jobs
    monkeypatch.setattr(career.llm, "GeminiLLM", lambda: FakeLLM())
    jd = project / "tests" / "sample_jd_iam.txt"
    assert jobs.main(["add", "--file", str(jd), "--company", "Acme", "--role", "IAM Analyst",
                      "--location", "Bengaluru", "--url", "https://example.com/1"]) == 0
    assert jobs.main(["add", "--file", str(jd), "--url", "https://example.com/1"]) == 1  # duplicate
    assert jobs.main(["apply", "J0001"]) == 0
    folder = next((project / "output").glob("J0001_*"))
    assert {p.name for p in folder.iterdir()} >= {"resume.pdf", "resume.docx", "answers.md", "report.md",
                                                  "analysis.json"}
    assert jobs.main(["status", "J0001", "applied"]) == 0
    assert jobs.main(["gaps"]) == 0
    assert jobs.main(["digest"]) == 0
    assert jobs.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Saved J0001" in out and "Already saved" in out and "now 'completed-manual'" in out
    from career.tracker import Tracker as T
    log = " | ".join(e["message"] for e in T(project).events())
    assert "resume generated" in log and "-> completed-manual" in log


def test_cli_offline_apply_and_base(project, capsys):
    import jobs
    jd = project / "tests" / "sample_jd_iam.txt"
    jobs.main(["add", "--file", str(jd), "--company", "Acme", "--role", "IAM", "--location", "Remote"])
    assert jobs.main(["apply", "J0001", "--offline"]) == 0
    assert jobs.main(["base"]) == 0
    assert (project / "output" / "base" / "Alex_Morgan_Security.pdf").exists()
    assert jobs.main(["show", "J0404"]) == 1


def test_cli_apply_reports_gemini_errors(project, monkeypatch, capsys):
    import career.llm
    import jobs

    def broken():
        raise career.llm.LLMError("Gemini rejected the API key.")
    monkeypatch.setattr(career.llm, "GeminiLLM", broken)
    jobs.main(["add", "--file", str(project / "tests" / "sample_jd_iam.txt"), "--company", "A", "--role", "R",
               "--location", "Bengaluru"])
    assert jobs.main(["apply", "J0001"]) == 2
    assert "--offline" in capsys.readouterr().out


def test_paste_mode_reads_until_end(project, monkeypatch, capsys):
    import jobs
    lines = iter(["IAM Analyst role in Bengaluru working on Azure AD, SSO, MFA and access reviews "
                  "for 0-2 years experience.", "More details here.", "END"])
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    assert jobs.main(["add", "--company", "A", "--role", "R", "--location", "Bengaluru", "--url", "u"]) == 0
    assert "Saved J0001" in capsys.readouterr().out
