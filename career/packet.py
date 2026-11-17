"""Build the application packet for one job: tailored resume (PDF + Word), report and form answers.

Used by `jobs.py apply`, the dashboard button and the agent loop, so all three behave the same.
The truthfulness guard runs inside analyze.run(); nothing here can add a claim.
"""

from __future__ import annotations

import json
from pathlib import Path

from career import fit as fitmod
from career import tracker as trk
from career.forms import answers_markdown
from career.profile import Profile
from career.render import MAX_SIZE, OnePageError, write_resume
from career.resume import base_content, layout

OK, NEEDS_DESCRIPTION, LLM_FAILED, LAYOUT_FAILED, BAD_REQUEST = 0, 1, 2, 3, 4


def packet_folder(tracker: trk.Tracker, row: dict[str, str]) -> Path:
    return tracker.root / "output" / f"{row['id']}_{trk.slug(row['company'], 20)}_{trk.slug(row['role'], 30)}"


def cover_note(tracker: trk.Tracker, row: dict[str, str]) -> str:
    path = tracker.root / row["output_dir"] / "analysis.json" if row.get("output_dir") else None
    if not path or not path.exists():
        return ""
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("model_output") or {}).get("cover_note", "") or ""
    except (ValueError, OSError):
        return ""


def build_packet(tracker: trk.Tracker, profile: Profile, job_id: str, *, offline: bool = False,
                 track: str | None = None, international: bool = False, say=print, llm=None) -> int:
    row = tracker.get(job_id)
    if row["status"] == "needs-description":
        say(f"{row['id']} has no full job description yet. Open {row['url'] or 'the listing'}, copy the "
            f"description, then run: python jobs.py attach {row['id']}  (or paste it in the UI)")
        return NEEDS_DESCRIPTION
    text = tracker.jd_text(row["id"])
    fit = fitmod.check(profile, text, row["location"], row["role"])
    international = international or fit.abroad
    folder = packet_folder(tracker, row)
    folder.mkdir(parents=True, exist_ok=True)
    track = track or row["track"]

    extra: dict[str, str] = {}
    cover, ats = "", ""
    if offline:
        doc = layout(profile, base_content(profile, track), international)
        (folder / "report.md").write_text(
            f"# {row['role']} @ {row['company']}\n\nOffline mode: base **{track}** resume, no AI analysis.\n"
            f"Keyword score: {row['keyword_score']}%. Fit: {fit.verdict}.\n", encoding="utf-8")
        stale = folder / "analysis.json"
        if stale.exists():
            stale.unlink()
        say("Offline mode: using your base resume (no Gemini call).")
    else:
        from career import analyze
        from career.llm import GeminiLLM, LLMError

        try:
            llm = llm or GeminiLLM()
            say(f"Analysing {row['id']} with Gemini (free tier)... this takes 20-60 seconds.")
            result = analyze.run(profile, llm, text, track=track, international=international)
        except LLMError as exc:
            say(f"\nGemini error: {exc}\n\nTip: `python jobs.py apply {row['id']} --offline` still gives you "
                "the base resume and form answers.")
            return LLM_FAILED
        doc, track = result.doc, result.track
        out = result.model_output
        cover = out.get("cover_note", "")
        extra = _save_analysis(folder, row, result, fit)
        ats = extra["ats_after"]
        say(f"  ATS (model estimate): {extra['ats_before'] or '-'} -> {ats or '-'}   "
            f"exact keywords: {result.keyword_before}% -> {result.keyword_after}%")
        say(f"  Rewritten bullets kept: {result.rewrites_kept}   guard notes: {len(result.guard_issues)}")

    if not _write_resume_files(tracker, row, folder, doc, say):
        return LAYOUT_FAILED
    (folder / "answers.md").write_text(answers_markdown(profile, row, fit, cover), encoding="utf-8")
    rel = folder.relative_to(tracker.root)
    new_status = "packet-ready" if row["status"] == "discovered" else row["status"]
    tracker.update(row["id"], status=new_status, output_dir=str(rel), track=track,
                   **({"ats_score": ats} if ats else {}), **extra)
    tracker.log(f"resume generated ({'offline' if offline else 'gemini'}, {track}) -> {rel}", row["id"])
    say(f"\nReady in {rel}/")
    say("  resume.pdf (1 page), resume.docx, answers.md, report.md")
    if fit.reasons:
        say("  Check first: " + "; ".join(fit.reasons))
    return OK


def revise_packet(tracker: trk.Tracker, profile: Profile, job_id: str, instruction: str, *, say=print,
                  llm=None) -> int:
    """Change a tailored resume the way you ask ("put the IAM bullets first"), re-check it and re-score it."""
    row = tracker.get(job_id)
    instruction = (instruction or "").strip()
    if not instruction:
        say("Say what to change, for example: \"Put the cloud project first and lead with Azure AD.\"")
        return BAD_REQUEST
    folder = tracker.root / row["output_dir"] if row["output_dir"] else None
    if not folder or not (folder / "resume.pdf").exists():
        say(f"{row['id']} has no tailored resume yet. Generate the packet first.")
        return BAD_REQUEST
    text = tracker.jd_text(row["id"])
    fit = fitmod.check(profile, text, row["location"], row["role"])
    track = row["track"] or next(iter(profile.tracks))
    analysis = folder / "analysis.json"
    previous = (json.loads(analysis.read_text(encoding="utf-8")) if analysis.exists()
                else {"track": track, "content": base_content(profile, track), "model_output": {}})

    from career import analyze
    from career.llm import GeminiLLM, LLMError

    try:
        llm = llm or GeminiLLM()
        say(f"Revising {row['id']} with Gemini: \"{instruction}\"... this takes 20-60 seconds.")
        result = analyze.revise(profile, llm, text, previous, instruction, international=fit.abroad)
    except LLMError as exc:
        say(f"\nGemini error: {exc}")
        return LLM_FAILED
    latest = result.model_output["revisions"][-1]
    extra = _save_analysis(folder, row, result, fit)
    if not _write_resume_files(tracker, row, folder, result.doc, say):
        return LAYOUT_FAILED
    (folder / "answers.md").write_text(
        answers_markdown(profile, row, fit, result.model_output.get("cover_note", "")), encoding="utf-8")
    tracker.update(row["id"], **({"ats_score": extra["ats_after"]} if extra["ats_after"] else {}), **extra)
    tracker.log(f"resume revised: \"{instruction[:80]}\" (ATS {latest['ats_before'] or '-'} -> "
                f"{latest['ats_after'] or '-'})", row["id"])
    for item in latest["changes_made"]:
        say(f"  done:      {item}")
    for item in latest["not_done"]:
        say(f"  not done:  {item}")
    for item in latest["guard_issues"]:
        say(f"  reverted:  {item}")
    say(f"  ATS (model estimate): {latest['ats_before'] or '-'} -> {latest['ats_after'] or '-'}   "
        f"exact keywords: {latest['keyword_before']}% -> {latest['keyword_after']}%")
    say(f"Updated {folder.relative_to(tracker.root)}/resume.pdf and resume.docx")
    return OK


def _save_analysis(folder: Path, row: dict[str, str], result, fit) -> dict[str, str]:
    """Write report.md and analysis.json; return the scores to store in the tracker."""
    from career import analyze

    out = result.model_output
    review = out.get("step9_recruiter_review") or {}
    (folder / "report.md").write_text(analyze.report_markdown(row, result, fit), encoding="utf-8")
    (folder / "analysis.json").write_text(json.dumps({
        "model": result.model_used, "track": result.track, "guard_issues": result.guard_issues,
        "keyword_before": result.keyword_before, "keyword_after": result.keyword_after,
        "content": result.content, "model_output": out}, indent=2), encoding="utf-8")
    return {"ats_before": str((out.get("step1_ats_before") or {}).get("overall", "")),
            "ats_after": str((out.get("step8_ats_after") or {}).get("overall", "")),
            "shortlist_pct": str((review.get("probability_percent") or {}).get("shortlist", ""))}


def _write_resume_files(tracker: trk.Tracker, row: dict[str, str], folder: Path, doc, say) -> bool:
    """Fit to one page and write the PDF and Word files. On failure, log why and leave no resume behind."""
    report = folder / "report.md"
    try:
        fitted = write_resume(doc, folder / "resume.pdf", folder / "resume.docx")
    except OnePageError as exc:
        for name in ("resume.pdf", "resume.docx"):  # don't leave an older resume that looks current
            (folder / name).unlink(missing_ok=True)
        with report.open("a", encoding="utf-8") as f:
            f.write(f"\n## Resume layout error\n\n{exc}\n")
        tracker.log(f"resume not saved: {exc}", row["id"], level="warn")
        tracker.notify("layout", f"{row['id']}: the resume does not fit on one page, so it was not saved. "
                                 "See its report.md.", row["id"])
        say(f"\nResume not saved: {exc}")
        return False
    if fitted.removed or fitted.size < MAX_SIZE:
        note = (f"Removed to fit: {', '.join(fitted.removed)}. " if fitted.removed else "") + f"Font: {fitted.size}pt."
        with report.open("a", encoding="utf-8") as f:
            f.write(f"\n## One-page fitting\n\n{note}\n")
        say(f"  One-page fitting: {note}")
    return True
