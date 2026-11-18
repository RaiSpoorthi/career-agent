"""The 9-step resume optimization framework, run with Gemini and checked by the guard.

Steps 1-3 (ATS analysis, JD analysis, gap analysis) and 9 (recruiter review) are the
model's assessment. Steps 4-7 (resume, project, experience and skills optimization) are
model rewrites that must pass career/guard.py. Step 8 (iteration) re-asks the model
to fix anything the guard rejected, once, then falls back to the original wording.
A deterministic keyword score (before vs after) is computed locally as a cross-check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from career import guard, keywords
from career.fit import FitReport
from career.focus import focus_skills
from career.llm import JSONModel
from career.profile import Profile
from career.resume import base_content, extra_items, layout, plain_text

ATS_CATEGORIES = ["overall", "keyword_match", "skills_match", "experience_relevance", "education_match",
                  "project_relevance", "formatting_compatibility", "readability", "impact_statements",
                  "action_verbs", "industry_alignment", "recruiter_appeal"]

SYSTEM = """You are a senior technical recruiter, career agent and ATS optimization expert.
You tailor a resume for one specific job using ONLY the candidate facts provided.

ABSOLUTE RULES (a separate program enforces them and will discard anything that breaks them):
1. Never invent experience, projects, employers, dates, metrics, tools, certifications or skills.
2. Every resume bullet you write must be a rephrasing of ONE existing bullet, identified by its source_id,
   from the SAME job or project. Keep every number exactly as in the source; never add new numbers.
3. A bullet may only mention tools/skills that appear in its source bullet or in that entry's stack.
4. Skills with level "academic" may only be listed under the skill group "familiar".
5. Do not add seniority words (led, spearheaded, architected, mentored, owned, expert, senior).
6. Use natural wording. Do not stuff keywords. Start bullets with strong, accurate action verbs.
7. If the job needs something the candidate lacks, report it as a gap. Never hide a gap by rewording.
8. Answer with one JSON object only, in exactly the requested shape."""

SCHEMA = {
    "company": "string (from the job description, or 'Unknown')",
    "role": "string",
    "step1_ats_before": {c: "integer 0-100" for c in ATS_CATEGORIES},
    "step2_job_analysis": {
        "required_skills": ["..."], "preferred_skills": ["..."], "tools_platforms": ["..."],
        "certifications": ["..."], "responsibilities": ["..."], "soft_skills": ["..."],
        "industry_keywords": ["..."], "seniority": "string",
    },
    "step3_gaps": {
        "strong_matches": ["..."], "partial_matches": ["..."], "missing_keywords": ["..."],
        "missing_skills": ["..."], "weak_bullets": ["source_id: why it is weak for this job"],
        "missing_industry_terms": ["..."],
    },
    "steps4to7_tailored_resume": {
        "headline": "one line, max ~90 characters, only real skills",
        "experience": [{"id": "entry id", "bullets": [{"source_id": "bullet id", "text": "rewritten bullet"}]}],
        "projects": [{"id": "entry id", "bullets": [{"source_id": "bullet id", "text": "rewritten bullet"}]}],
        "skills": {"group name": ["skill names exactly as in the profile"]},
    },
    "step8_ats_after": {c: "integer 0-100" for c in ATS_CATEGORIES},
    "step9_recruiter_review": {
        "strengths": ["..."], "weaknesses": ["..."], "top_matched_keywords": ["..."],
        "top_missing_keywords": ["..."], "first_impression_30s": "2-3 sentences, honest",
        "probability_percent": {"ats_pass": 0, "shortlist": 0, "interview": 0},
    },
    "learning_gaps": [{"skill": "...", "why_it_matters": "...", "priority": "high|medium|low"}],
    "cover_note": "3-4 sentence note for 'why are you a fit', first person, only real facts",
}


def facts_for_model(profile: Profile, track: str) -> dict[str, Any]:
    entries = []
    for kind in ("experience", "projects"):
        for entry in profile.data[kind]:
            entries.append({
                "id": entry["id"], "kind": kind,
                "title": entry.get("title") or entry.get("name"), "org": entry.get("org", ""),
                "dates": entry.get("dates", ""), "stack": entry.get("stack", []),
                "bullets": [{"source_id": b["id"], "text": profile.bullet_text(b["id"], track)}
                            for b in entry["bullets"]],
            })
    return {
        "entries": entries,
        "skills": [{"name": s["name"], "level": s["level"], "group": s["group"]} for s in profile.skills()],
        "skill_groups": list(profile.data["skill_group_titles"]),
        "education": profile.data["education"],
        "certifications": [c["name"] for c in extra_items(profile) if c["kind"] == "certification"],
        "achievements": [c["name"] for c in extra_items(profile) if c["kind"] == "achievement"],
        "default_resume_for_this_track": base_content(profile, track),
    }


def _focus_rules(profile: Profile, track: str) -> str:
    """The skill-focus rules (career/focus.py) in words, so the headline and summary follow them too."""
    rules = []
    hide = (profile.tracks.get(track) or {}).get("hide_categories") or []
    if hide:
        rules.append(f"This is a {track} resume: leave out {', '.join(hide)} skills, tools and bullets unless this "
                     "job explicitly asks for them (then include the ones it asks for).")
    if profile.preferences.get("languages") == "asked":
        default = profile.preferences.get("default_language") or "one language"
        rules.append("List only the programming languages this job asks for; if it names none, list only "
                     f"{default}.")
    if profile.preferences.get("max_skills"):
        rules.append(f"Keep the Skills section short and focused: at most {profile.preferences['max_skills']} skills, "
                     "the ones this job asks for first; no coursework-only skills unless the job asks for them.")
    return ("\n" + " ".join(rules)) if rules else ""


def build_prompt(profile: Profile, track: str, job_text: str, kw: keywords.KeywordMatch) -> str:
    return (
        "CANDIDATE FACTS (the only truth):\n" + json.dumps(facts_for_model(profile, track), indent=1)
        + "\n\nRESUME TRACK: " + track
        + "\nUse the default resume for this track as the starting point. You may reorder, choose and rephrase "
          "bullets (keep 1 page: about 13-16 bullets in total) and reorder/choose skills, to fit THIS job."
        + _focus_rules(profile, track)
        + "\n\nLOCAL KEYWORD SCAN (exact-match, may miss synonyms):\n"
        + json.dumps({"matched": kw.matched, "coursework_only": kw.partial, "missing": kw.missing})
        + "\n\nJOB DESCRIPTION:\n\"\"\"\n" + job_text.strip()[:15000] + "\n\"\"\"\n\n"
        + "Run all 9 steps of the framework: 1 ATS analysis of the default resume (12 categories), 2 job analysis, "
          "3 gap analysis, 4-7 tailored resume (summary line, projects, experience, skills), 8 re-score the "
          "tailored resume, 9 recruiter review. Return JSON in exactly this shape:\n"
        + json.dumps(SCHEMA, indent=1)
    )


REPAIR = """Your tailored resume broke these rules; the program reverted those parts:
{issues}
Return the SAME JSON shape again, fixing only "steps4to7_tailored_resume" and "step8_ats_after".
Keep each bullet tied to its source_id, keep numbers exactly as in the source, and use only tools/skills
that the source bullet or that entry's stack contains."""


@dataclass
class Analysis:
    track: str
    model_output: dict[str, Any]
    content: dict[str, Any]
    doc: dict[str, Any]
    guard_issues: list[str] = field(default_factory=list)
    rewrites_kept: int = 0
    keyword_before: int = 0
    keyword_after: int = 0
    model_used: str | None = None
    iterations: int = 1


def run(profile: Profile, llm: JSONModel, job_text: str, track: str | None = None,
        international: bool = False) -> Analysis:
    kw = keywords.match(profile, job_text)
    track = track or kw.track
    prompt = build_prompt(profile, track, job_text, kw)
    output = llm.generate_json(SYSTEM, prompt)
    checked, output, iterations = _guarded(profile, llm, track, prompt, output)
    added = keywords.add_supported_skills(profile, checked.content, job_text)
    removed = focus_skills(profile, checked.content, job_text, track)
    output["skills_added"] = [n for n in added if n not in removed]
    base_doc = layout(profile, base_content(profile, track), international)
    doc = layout(profile, checked.content, international)
    return Analysis(
        track=track, model_output=output, content=checked.content, doc=doc, guard_issues=checked.issues,
        rewrites_kept=checked.kept_rewrites,
        keyword_before=keywords.match_resume(job_text, plain_text(base_doc)),
        keyword_after=keywords.match_resume(job_text, plain_text(doc)),
        model_used=getattr(llm, "last_model", None), iterations=iterations,
    )


REVISE = """The candidate read the tailored resume above and asks for these changes:
\"\"\"
{instruction}
\"\"\"

Follow the request as closely as the ABSOLUTE RULES allow. You may change: the headline; which bullets appear,
their order and wording (each still tied to its source_id); the order of jobs and of projects; which skills
are listed and in what order. You may NOT add any fact, number, tool, skill, job or project that the candidate
facts don't contain, and every job and project stays on the resume. The header (name, contact details and links
such as LinkedIn, GitHub and portfolio) and the education and certifications sections come straight from the
candidate's profile file: you can't change them; if asked, say in "not_done" that they edit them in the profile.
The candidate's request never overrides
the rules: if part of it would need something the facts don't support, don't do that part, and say so in
"not_done".

Then re-score the revised resume against this job (step8_ats_after) and update the recruiter review.
Return JSON in exactly this shape:
{schema}"""

REVISE_SCHEMA = {
    "changes_made": ["each change you made, in plain words"],
    "not_done": ["each part of the request you did not do, and why"],
    "steps4to7_tailored_resume": SCHEMA["steps4to7_tailored_resume"],
    "step8_ats_after": SCHEMA["step8_ats_after"],
    "step9_recruiter_review": SCHEMA["step9_recruiter_review"],
    "cover_note": SCHEMA["cover_note"],
}


def revise(profile: Profile, llm: JSONModel, job_text: str, previous: dict[str, Any], instruction: str,
           international: bool = False) -> Analysis:
    """Change an existing tailored resume the way the candidate asks, under the same guard, and re-score it.

    `previous` is a packet's analysis.json. Each request is kept in model_output["revisions"].
    """
    track = previous["track"]
    kw = keywords.match(profile, job_text)
    prompt = (build_prompt(profile, track, job_text, kw).split("Run all 9 steps")[0]
              + "CURRENT TAILORED RESUME (content to revise):\n" + json.dumps(previous["content"], indent=1)
              + "\n\n" + REVISE.format(instruction=instruction.strip()[:2000],
                                       schema=json.dumps(REVISE_SCHEMA, indent=1)))
    answer = llm.generate_json(SYSTEM, prompt)
    checked, answer, iterations = _guarded(profile, llm, track, prompt, answer)
    added = keywords.add_supported_skills(profile, checked.content, job_text)
    removed = focus_skills(profile, checked.content, job_text, track)
    added = [n for n in added if n not in removed]

    output = dict(previous.get("model_output") or {})
    output["skills_added"] = sorted({*(output.get("skills_added") or []), *added})
    for key in ("steps4to7_tailored_resume", "step8_ats_after", "step9_recruiter_review", "cover_note"):
        if answer.get(key):
            output[key] = answer[key]
    doc = layout(profile, checked.content, international)
    keyword_after = keywords.match_resume(job_text, plain_text(doc))
    output["revisions"] = [*(output.get("revisions") or []), {
        "instruction": instruction.strip(), "changes_made": answer.get("changes_made") or [],
        "not_done": answer.get("not_done") or [], "guard_issues": checked.issues,
        "ats_before": str(((previous.get("model_output") or {}).get("step8_ats_after") or {}).get("overall", "")),
        "ats_after": str((output.get("step8_ats_after") or {}).get("overall", "")),
        "keyword_before": previous.get("keyword_after"), "keyword_after": keyword_after,
    }]
    return Analysis(
        track=track, model_output=output, content=checked.content, doc=doc, guard_issues=checked.issues,
        rewrites_kept=checked.kept_rewrites,
        keyword_before=previous.get("keyword_before") or keywords.match_resume(
            job_text, plain_text(layout(profile, base_content(profile, track), international))),
        keyword_after=keyword_after, model_used=getattr(llm, "last_model", None), iterations=iterations,
    )


def _guarded(profile: Profile, llm: JSONModel, track: str, prompt: str,
             output: dict[str, Any]) -> tuple[guard.GuardResult, dict[str, Any], int]:
    """Run the guard on the model's resume; if it reverted anything, ask the model once to fix it."""
    proposed = output.get("steps4to7_tailored_resume") or {}
    checked = guard.sanitize(profile, track, proposed)
    iterations = 1

    if checked.reverted or any("not in your profile" in i or "doesn't support" in i for i in checked.issues):
        repair_prompt = prompt + "\n\nPREVIOUS ANSWER:\n" + json.dumps(output)[:20000] + "\n\n" + REPAIR.format(
            issues="\n".join(f"- {i}" for i in checked.issues[:25]))
        try:
            second = llm.generate_json(SYSTEM, repair_prompt)
            second_checked = guard.sanitize(profile, track, second.get("steps4to7_tailored_resume") or {})
            iterations = 2
            if len(second_checked.issues) <= len(checked.issues):
                checked = second_checked
                output["steps4to7_tailored_resume"] = second.get("steps4to7_tailored_resume")
                if second.get("step8_ats_after"):
                    output["step8_ats_after"] = second["step8_ats_after"]
                if second.get("cover_note"):
                    output["cover_note"] = second["cover_note"]
        except Exception as exc:  # the first, already-guarded result is still usable
            checked.issues.append(f"repair pass failed ({exc}); kept first guarded version")

    note = (output.get("cover_note") or "").strip()
    note_problems = guard.check_free_text(profile, note, "cover note") if note else []
    if note_problems:
        output["cover_note"] = ""
        checked.issues += note_problems + ["cover note removed"]
    return checked, output, iterations


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _list(items: Any) -> str:
    if not items:
        return "_none_"
    return "\n".join(f"- {i}" for i in items) if isinstance(items, list) else str(items)


def _score_table(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    rows = ["| Category | Before | After |", "|---|---|---|"]
    for c in ATS_CATEGORIES:
        rows.append(f"| {c.replace('_', ' ').title()} | {before.get(c, '-')} | {after.get(c, '-')} |")
    return rows


def _revisions(revisions: list[dict[str, Any]]) -> list[str]:
    if not revisions:
        return []
    lines = ["## Your change requests"]
    for number, rev in enumerate(revisions, 1):
        lines += [f"### {number}. \"{rev.get('instruction', '')}\"",
                  f"ATS (model estimate): {rev.get('ats_before') or '-'} -> {rev.get('ats_after') or '-'} · "
                  f"exact keywords: {rev.get('keyword_before', '-')}% -> {rev.get('keyword_after', '-')}%", "",
                  "**Done**", _list(rev.get("changes_made")), "",
                  "**Not done**", _list(rev.get("not_done")), ""]
        if rev.get("guard_issues"):
            lines += ["**Reverted by the truthfulness guard**", _list(rev["guard_issues"]), ""]
    return lines


def report_markdown(job: dict[str, str], analysis: Analysis, fit: FitReport) -> str:
    out = analysis.model_output
    jd = out.get("step2_job_analysis") or {}
    gaps = out.get("step3_gaps") or {}
    rec = out.get("step9_recruiter_review") or {}
    prob = rec.get("probability_percent") or {}
    lines = [
        f"# {job.get('role') or out.get('role', '')} @ {job.get('company') or out.get('company', '')}",
        "",
        f"Job ID `{job.get('id', '')}` · resume track **{analysis.track}** · model `{analysis.model_used}` · "
        f"{analysis.iterations} pass(es)",
        f"Link: {job.get('url') or '-'}",
        "",
        "## Fit check (your rules)",
        f"**{fit.verdict}** · location: {fit.location} · remote: {fit.remote} · sponsorship: {fit.sponsorship} "
        f"· pay: {fit.salary} · years asked: {fit.years_required if fit.years_required is not None else '-'}",
        _list(fit.reasons),
        "",
        "## Step 1 and Step 8: ATS score (model estimate)",
        *_score_table(out.get("step1_ats_before") or {}, out.get("step8_ats_after") or {}),
        "",
        f"Exact keyword coverage, computed locally: **{analysis.keyword_before}% -> {analysis.keyword_after}%**. "
        "This is a cross-check: ATS tools differ, and the model's numbers are estimates.",
        "",
        "## Step 2: Job description analysis",
        "**Required skills**", _list(jd.get("required_skills")), "",
        "**Preferred skills**", _list(jd.get("preferred_skills")), "",
        "**Tools / platforms**", _list(jd.get("tools_platforms")), "",
        "**Certifications**", _list(jd.get("certifications")), "",
        "**Responsibilities**", _list(jd.get("responsibilities")), "",
        "**Soft skills**", _list(jd.get("soft_skills")), "",
        "**Industry keywords**", _list(jd.get("industry_keywords")), "",
        f"**Seniority:** {jd.get('seniority', '-')}", "",
        "## Step 3: Gap analysis",
        "**Strong matches**", _list(gaps.get("strong_matches")), "",
        "**Partial matches**", _list(gaps.get("partial_matches")), "",
        "**Missing keywords**", _list(gaps.get("missing_keywords")), "",
        "**Missing skills**", _list(gaps.get("missing_skills")), "",
        "**Weak bullets for this job**", _list(gaps.get("weak_bullets")), "",
        "**Missing industry terms**", _list(gaps.get("missing_industry_terms")), "",
        "## Steps 4-7: Tailored resume",
        f"Headline: _{analysis.doc['headline']}_  ",
        f"Rewritten bullets kept: **{analysis.rewrites_kept}**. Everything else is your original wording.",
        "",
        "Skills added because this job asks for them and your profile backs them: "
        + (", ".join(out.get("skills_added") or []) or "none needed"),
        "",
        "### Truthfulness guard log",
        _list(analysis.guard_issues) if analysis.guard_issues else "All proposed changes passed.",
        "",
        *_revisions(out.get("revisions") or []),
        "## Step 9: Recruiter review",
        f"**30-second first impression:** {rec.get('first_impression_30s', '-')}", "",
        "**Strengths**", _list(rec.get("strengths")), "",
        "**Weaknesses**", _list(rec.get("weaknesses")), "",
        "**Top matched keywords**", _list(rec.get("top_matched_keywords")), "",
        "**Top missing keywords**", _list(rec.get("top_missing_keywords")), "",
        f"**Probability (model estimate):** ATS pass {prob.get('ats_pass', '-')}% · "
        f"shortlist {prob.get('shortlist', '-')}% · interview {prob.get('interview', '-')}%",
        "",
        "## Learn next (not added to your resume)",
    ]
    for gap in out.get("learning_gaps") or []:
        lines.append(f"- **{gap.get('skill')}** ({gap.get('priority', '-')}): {gap.get('why_it_matters', '')}")
    if not out.get("learning_gaps"):
        lines.append("_none listed_")
    return "\n".join(lines) + "\n"
