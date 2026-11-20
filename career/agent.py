"""The autonomous loop: find jobs, tailor a resume for the good ones, apply, and hand off what needs you.

One pass (`python jobs.py run`, or the Run button on the dashboard):
  1. Discover   - job boards, career pages, alert emails. Free, no AI.
  2. Tailor     - for listings your rules mark APPLY and that match enough keywords, build the packet
                  with Gemini (free tier), a few per run, with a pause between calls.
  3. Apply      - apply_mode "auto": fill and submit supported forms; anything unclear goes to "Needs you".
                  apply_mode "review": nothing is submitted; every packet goes to "Needs you".
                  apply_mode "off": stop after tailoring.
"""

from __future__ import annotations

import time

from career import autoapply, jdfetch, packet, pipeline, sources
from career.profile import Profile, personal_file
from career.tracker import SUBMITTED, Tracker


def _int(settings: dict[str, str], key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def eligible(row: dict[str, str], minimum: int, fits: tuple[str, ...] = ("APPLY",)) -> bool:
    return row["fit"] in fits and int(row["keyword_score"] or 0) >= minimum


def tailored_fits(settings: dict[str, str]) -> tuple[str, ...]:
    """Which fit verdicts get a resume automatically (setting `tailor_fits`). REVIEW jobs are never auto-submitted."""
    return ("APPLY", "REVIEW") if settings.get("tailor_fits") == "APPLY+REVIEW" else ("APPLY",)


def _check_first(row: dict[str, str]) -> str:
    return f"Check first: {row['notes']}. " if row["fit"] == "REVIEW" and row["notes"] else ""


def run_once(tracker: Tracker, profile: Profile, say=print, *, discover=None, build=packet.build_packet,
             apply=autoapply.apply_to_job, sleep=time.sleep) -> dict[str, int]:
    settings = tracker.settings()
    minimum = _int(settings, "min_keyword_score", 45)
    stats = {"found": 0, "packets": 0, "submitted": 0, "needs_you": 0}
    tracker.log("run started", level="run")

    # ---- 1. discover ----------------------------------------------------------------------
    say("Step 1/3  Looking for jobs")
    if discover is None:
        cfg = pipeline.load_config(personal_file(pipeline.CONFIG, tracker.root))
        found, notes = pipeline.discover(cfg)
        inbox, inbox_notes = sources.read_inbox(tracker.root / "inbox")
        found, notes = found + inbox, notes + inbox_notes
        try:  # posts you forwarded to your own Telegram bot, if you set one up
            from career import telegrambot
            if telegrambot.token():
                found += telegrambot.collect(tracker, say=say)
        except Exception as exc:
            notes.append(f"Telegram bot: {str(exc)[:160]}")
        try:  # Gmail, if an app password (or the Google API) is set up
            from career.gmail import fetch_alerts
            for raw in fetch_alerts(tracker.root):
                found += sources.parse_alert_eml(raw)
        except Exception as exc:
            notes.append(f"Gmail: {str(exc)[:160]}")
    else:
        found, notes = discover()
    counts = pipeline.ingest(tracker, profile, found)
    if discover is None and tracker.rows("needs-description"):
        say("  Fetching full descriptions for links that came with only a title...")
        got = jdfetch.fill_missing(tracker, profile, say=say)
        say(f"  {got['found']} description(s) found; {got['not_found']} still need you to paste them")
    stats["found"] = counts["new"]
    rescored = pipeline.rescore(tracker, profile)  # your current rules also apply to jobs found earlier
    if rescored["now_skipped"]:
        say(f"  {rescored['now_skipped']} earlier job(s) now break your rules and were skipped")
    for note in notes:
        say(f"  note: {note}")
        tracker.log(note, level="warn")
    say(f"  {len(found)} listing(s) checked: {counts['new']} new, {counts['auto_skipped']} skipped by your rules, "
        f"{counts['needs_description']} need a description, {counts['duplicate']} already seen")

    # ---- 2. tailor ------------------------------------------------------------------------
    say("Step 2/3  Tailoring resumes")
    fits = tailored_fits(settings)
    # APPLY jobs first, then by keyword match
    todo = sorted((r for r in tracker.rows("discovered") if eligible(r, minimum, fits)),
                  key=lambda r: (r["fit"] != "APPLY", -int(r["keyword_score"] or 0)))
    todo = todo[: _int(settings, "max_packets_per_run", 8)]
    offline = settings["use_gemini"] != "on"
    for index, row in enumerate(todo):
        if index and not offline:
            sleep(_int(settings, "gemini_gap_seconds", 12))
        code = build(tracker, profile, row["id"], offline=offline, say=say)
        if code == packet.LLM_FAILED:
            tracker.notify("gemini", "Gemini stopped answering (often the free daily limit). Tailoring resumes "
                                     "again on the next run.")
            break
        if code == packet.OK:
            stats["packets"] += 1
    if not todo:
        say("  nothing new that meets your rules")

    # ---- 3. apply -------------------------------------------------------------------------
    mode = settings["apply_mode"]
    ready = [r for r in tracker.rows("packet-ready") if eligible(r, minimum, fits)]
    say(f"Step 3/3  Applying (mode: {mode})")
    if mode == "review":
        for row in ready:
            kind = autoapply.host_kind(row["apply_url"] or row["url"])
            reason = ("Review mode: press Take over to open the filled form, then press Submit."
                      if kind in ("supported", "unsupported") else
                      "This site needs your account, so it is never automated. Open the listing and apply with "
                      "the packet.")
            tracker.update(row["id"], status="awaiting-human", handoff_reason=_check_first(row) + reason)
            stats["needs_you"] += 1
    elif mode == "auto":
        for row in [r for r in ready if r["fit"] != "APPLY"]:  # a REVIEW job is never submitted for you
            tracker.update(row["id"], status="awaiting-human",
                           handoff_reason=_check_first(row) + "Press Take over if you still want it.")
            stats["needs_you"] += 1
        ready = [r for r in ready if r["fit"] == "APPLY"]
        room = _int(settings, "max_applications_per_day", 10) - tracker.submitted_today()
        if ready and room <= 0:
            say("  daily application limit reached; the rest wait for the next run")
        for row in ready[: max(room, 0)]:
            try:
                result = apply(tracker, profile, row["id"], mode="auto", say=say)
            except autoapply.Unavailable as exc:
                tracker.notify("setup", str(exc))
                say(f"  {exc}")
                break
            stats["submitted" if result.outcome == "submitted" else "needs_you"] += 1
            sleep(5)
    if stats["needs_you"]:
        tracker.notify("summary", f"{stats['needs_you']} application(s) are waiting for you on the Needs you page.")
    summary = (f"Run finished: {stats['found']} new job(s), {stats['packets']} resume(s) tailored, "
               f"{stats['submitted']} submitted, {stats['needs_you']} waiting for you")
    say(summary)
    tracker.log(summary, level="run")
    return stats


def apply_now(tracker: Tracker, profile: Profile, job_id: str, say=print, *, build=packet.build_packet,
              apply=autoapply.apply_to_job) -> str:
    """Tailor and apply to one job you picked yourself, following the same rules as a run.

    Builds the packet if there isn't one, then follows apply_mode: "review" opens the filled form for you
    to submit, "auto" submits only when everything is answered (within the daily limit and never for a job
    your rules mark SKIP), "off" stops at the packet. Returns a short outcome word.
    """
    row = tracker.get(job_id)
    if row["status"] in SUBMITTED:
        say(f"{row['id']} is already marked submitted ({row['status']}).")
        return "already-submitted"
    settings = tracker.settings()
    has_packet = row["output_dir"] and (tracker.root / row["output_dir"] / "resume.pdf").exists()
    if not has_packet:
        say(f"Tailoring the resume for {row['id']}: {row['role']} @ {row['company']}")
        code = build(tracker, profile, row["id"], offline=settings["use_gemini"] != "on", say=say)
        if code != packet.OK:
            return "no-packet"
        row = tracker.get(row["id"])

    mode = settings["apply_mode"]
    say(f"Applying (mode: {mode})")
    if mode == "off":
        say("  apply_mode is off: the packet is ready, nothing was opened.")
        return "packet-ready"
    if mode == "auto" and row["fit"] == "SKIP":
        reason = "Your rules flag this job (see Check first). Review it, then press Take over if you still want it."
        tracker.update(row["id"], status="awaiting-human", handoff_reason=reason)
        say(f"  {row['id']} needs you: {reason}")
        return "handoff"
    if mode == "auto" and tracker.submitted_today() >= _int(settings, "max_applications_per_day", 10):
        reason = "Today's automatic submission limit is reached. Press Take over to apply yourself."
        tracker.update(row["id"], status="awaiting-human", handoff_reason=reason)
        say(f"  {reason}")
        return "handoff"
    try:
        result = apply(tracker, profile, row["id"], mode="auto" if mode == "auto" else "takeover", say=say)
    except autoapply.Unavailable as exc:
        tracker.notify("setup", str(exc))
        say(f"  {exc}")
        return "unavailable"
    return result.outcome
