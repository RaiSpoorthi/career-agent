"""Career agent command line.

First time:
  python jobs.py init                     create your profile and config files from the examples

The agent (does everything below in one pass):
  python jobs.py run [--mode auto]        find jobs, tailor resumes, apply; anything unclear waits for you
  python jobs.py takeover J0001           open the application form already filled in; you press Submit
  python jobs.py auto-apply J0001         fill and submit one application (stops if anything is unanswered)
  python jobs.py add --url URL --apply    a job you found: save it, tailor the resume, apply (per apply_mode)
  python jobs.py apply-now J0001          tailor (if needed) and apply to one saved job (per apply_mode)
  python jobs.py revise J0001 "..."       tell it what to change in a tailored resume; it re-checks the ATS score
  python jobs.py rescore                  re-check saved jobs after you change your rules
  python jobs.py ui                       dashboard at http://127.0.0.1:5055 (Needs you, logs, settings)
  python jobs.py telegram-setup           connect your Telegram bot: forward job posts to it

Free (no AI, no internet):
  python jobs.py add                      save a job (paste its description), get keyword score + fit check
  python jobs.py list [--status new]      tracker
  python jobs.py show J0001               details and keyword gaps for one job
  python jobs.py status J0001 applied     update status (see `jobs.py status -h` for the list)
  python jobs.py digest                   what to apply to next
  python jobs.py gaps                     learning-gap summary across all saved jobs
  python jobs.py base                     render your two base resumes (security, software)
  python jobs.py apply J0001 --offline    base resume + form answers, no AI

Finding jobs (free, no AI):
  python jobs.py discover                 check the job boards and career pages in config/sources.yaml
  python jobs.py ingest-inbox [--gmail]   read LinkedIn/Naukri alert emails (inbox/ folder, or Gmail)
  python jobs.py attach J0001             paste the full description for a listing from an alert
  python jobs.py applied J0001            mark applied today (or --date YYYY-MM-DD)
  python jobs.py ui                       open the dashboard at http://127.0.0.1:5055

Uses your free Gemini key:
  python jobs.py apply J0001              full 9-step analysis + tailored resume (PDF + Word) + form answers
  python jobs.py check-key                test the Gemini key
"""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap
from pathlib import Path

from career import fit as fitmod
from career import keywords, tracker
from career.profile import PROFILE_FILE, ROOT, Profile, example_of
from career.render import OnePageError, write_resume
from career.resume import base_content, layout


def say(text: str = "") -> None:
    print(text, flush=True)


def _profile() -> Profile:
    return Profile.load()


def _read_pasted() -> str:
    say("Paste the full job description. When done, type END on its own line and press Enter:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _ask(label: str, current: str | None) -> str:
    if current:
        return current
    try:
        return input(f"{label}: ").strip()
    except EOFError:
        return ""


def _print_match(kw: keywords.KeywordMatch, fit: fitmod.FitReport) -> None:
    say(f"  Best resume: {kw.track}   Keyword score: {kw.score}% (confidence {kw.confidence})")
    say(f"  Matched:  {', '.join(kw.matched) or '-'}")
    if kw.partial:
        say(f"  Coursework only: {', '.join(kw.partial)}")
    say(f"  Missing:  {', '.join(kw.missing) or '-'}")
    say(f"  Pay: {fit.salary if fit.salary != 'not stated' else 'not stated in the posting'}")
    say(f"  Fit: {fit.verdict}  | location: {fit.location} | remote: {fit.remote} | sponsorship: {fit.sponsorship}")
    for reason in fit.reasons:
        say(f"    - {reason}")


# ---------------------------------------------------------------------------
def cmd_add(args) -> int:
    profile, t = _profile(), tracker.Tracker()
    if args.url and (dup := t.find_url(args.url)):
        say(f"Already saved as {dup['id']} ({dup['role']} @ {dup['company']}).")
        if args.apply:
            return cmd_apply_now(argparse.Namespace(job_id=dup["id"]))
        return 1
    text = Path(args.file).read_text(encoding="utf-8") if args.file else _read_pasted()
    if len(text) < 80:
        say("That job description looks too short. Paste the full text.")
        return 1
    interactive = not args.file  # --file mode never stops to ask questions
    company = _ask("Company", args.company) if interactive else (args.company or "")
    role = _ask("Role / job title", args.role) if interactive else (args.role or "")
    location = (_ask("Location (e.g. Bengaluru, Remote, Berlin)", args.location) if interactive
                else (args.location or ""))
    url = args.url or (_ask("Job link (optional)", None) if interactive else "")

    from career.pipeline import add_manual

    row, _ = add_manual(t, profile, url=url, text=text, company=company, role=role, location=location)
    fit = fitmod.check(profile, text, row["location"], row["role"])
    say(f"\nSaved {row['id']}: {row['role']} @ {row['company']}")
    _print_match(keywords.match(profile, text), fit)
    if args.apply:
        from career import agent

        outcome = agent.apply_now(t, profile, row["id"], say)
        return 0 if outcome not in ("no-packet", "unavailable") else 2
    if fit.verdict == fitmod.SKIP:
        say(f"\n  Suggestion: python jobs.py status {row['id']} skipped")
    else:
        say(f"\n  Next: python jobs.py apply {row['id']}")
    return 0


def cmd_list(args) -> int:
    rows = tracker.Tracker().rows()
    if args.status:
        rows = [r for r in rows if r["status"] == tracker.normal_status(args.status)]
    if not rows:
        say("No jobs yet. Add one with: python jobs.py add")
        return 0
    say(f"{'ID':<6} {'Status':<17} {'Fit':<7} {'KW':>4} {'ATS':>4}  {'Pay':<20} {'Location':<22} Role @ Company")
    for r in rows:
        say(f"{r['id']:<6} {r['status']:<17} {r['fit']:<7} {r['keyword_score']:>4} {r['ats_score'] or '-':>4}  "
            f"{_pay(r):<20} {r['location'][:22]:<22} {r['role'][:34]} @ {r['company'][:20]}")
    if any(not r["salary"] for r in rows):
        say("\nPay '-' = the posting doesn't state it (most don't). Ask in the first HR call.")
    return 0


def _pay(row: dict[str, str]) -> str:
    return (row.get("salary") or "-")[:20]


def cmd_show(args) -> int:
    profile, t = _profile(), tracker.Tracker()
    row = t.get(args.job_id)
    text = t.jd_text(row["id"])
    say(f"{row['id']}: {row['role']} @ {row['company']}  [{row['status']}]")
    say(f"  Pay: {fitmod.stated_pay(text) or 'not stated in the posting'}   Location: {row['location'] or '-'}")
    say(f"  Link: {row['url'] or '-'}\n  Added: {row['added']}  Updated: {row['updated']}")
    if row["output_dir"]:
        say(f"  Files: {row['output_dir']}")
    _print_match(keywords.match(profile, text), fitmod.check(profile, text, row["location"], row["role"]))
    return 0


def cmd_status(args) -> int:
    row = tracker.Tracker().update(args.job_id, status=args.status, **({"notes": args.note} if args.note else {}))
    say(f"{row['id']} is now '{row['status']}'.")
    return 0


def cmd_gaps(args) -> int:
    t = tracker.Tracker()
    text = t.gap_summary(_profile())
    say(text)
    say(f"(saved to {t.gap_path.relative_to(ROOT)})")
    return 0


def cmd_base(args) -> int:
    profile = _profile()
    out = ROOT / "output" / "base"
    out.mkdir(parents=True, exist_ok=True)
    failed = False
    for track in profile.tracks:
        doc = layout(profile, base_content(profile, track), international=args.international)
        name = f"{profile.contact['name'].replace(' ', '_')}_{track.title()}"
        try:
            fitted = write_resume(doc, out / f"{name}.pdf", out / f"{name}.docx")
        except OnePageError as exc:
            say(f"  {track:<9} -> not saved: {exc}")
            failed = True
            continue
        dropped = f", removed: {', '.join(fitted.removed)}" if fitted.removed else ""
        say(f"  {track:<9} -> output/base/{name}.pdf / .docx (1 page, {fitted.size}pt{dropped})")
    return 1 if failed else 0


def cmd_init(args) -> int:
    """First-time setup: copy each example file you still need. Never overwrites one you have."""
    for rel in (PROFILE_FILE, "config/answers.yaml", "config/sources.yaml", ".env"):
        path = ROOT / rel
        if path.exists():
            say(f"  kept     {rel} (you already have it)")
        else:
            shutil.copyfile(example_of(path), path)
            say(f"  created  {rel}")
    say(textwrap.dedent(f"""
        Next:
          1. .env: paste your free Gemini key (https://aistudio.google.com/apikey).
          2. {PROFILE_FILE}: replace the sample person with your own details, tracks and job-search
             preferences, then delete the `sample: true` line. Only what is in this file can appear on a resume.
          3. config/sources.yaml: the companies to search. config/answers.yaml: form answers only you know.
          4. Check it:  python jobs.py check-key   then   python jobs.py base   (your base resumes)
          5. Start:     python jobs.py ui          (dashboard at http://127.0.0.1:5055)"""))
    return 0


def cmd_apply(args) -> int:
    from career.packet import build_packet

    if args.track and args.track not in (tracks := _profile().tracks):
        say(f"Unknown track '{args.track}'. Your profile has: {', '.join(tracks)}")
        return 1
    code = build_packet(tracker.Tracker(), _profile(), args.job_id, offline=args.offline, track=args.track,
                        international=args.international, say=say)
    if code == 0:
        say(f"  Next: python jobs.py takeover {args.job_id.upper()}   (opens the filled form), or apply by hand")
    return code


def cmd_digest(args) -> int:
    rows = tracker.Tracker().rows()
    order = {"APPLY": 0, "REVIEW": 1, "SKIP": 2}
    todo = [r for r in rows if r["status"] in ("discovered", "packet-ready", "awaiting-human", "needs-description")]
    todo.sort(key=lambda r: (order.get(r["fit"], 3), -int(r["keyword_score"] or 0)))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in tracker.STATUSES}
    say("Pipeline: " + ", ".join(f"{s} {n}" for s, n in counts.items() if n) if rows else "Pipeline: empty")
    if not todo:
        say("Nothing waiting. Add jobs with: python jobs.py add")
        return 0
    say("\nNext up (best fit first):")
    for r in todo[: args.limit]:
        action = (f"python jobs.py takeover {r['id']}" if r["status"] in ("packet-ready", "awaiting-human")
                  else f"python jobs.py apply {r['id']}")
        if r["status"] == "needs-description":
            action = f"python jobs.py attach {r['id']}"
        if r["fit"] == "SKIP":
            action = f"python jobs.py status {r['id']} skipped"
        say(f"  {r['id']} [{r['fit']:<6}] {r['keyword_score']:>3}%  pay {_pay(r):<14} {r['role'][:34]} @ "
            f"{r['company'][:20]}  -> {action}")
    return 0


def cmd_check_key(args) -> int:
    from career.llm import GeminiLLM, LLMError

    try:
        llm = GeminiLLM()
        reply = llm.generate_json("Answer in JSON.", 'Return {"ok": true}')
    except LLMError as exc:
        say(f"Not working: {exc}")
        return 2
    say(f"Gemini key works (model {llm.last_model}): {reply}")
    return 0


def cmd_discover(args) -> int:
    from career import pipeline

    profile, t = _profile(), tracker.Tracker()
    cfg = pipeline.load_config()
    if not any(cfg.get(k) for k in pipeline.SOURCE_KINDS):
        say("No sources configured yet. Add companies to config/sources.yaml (see the examples there).")
    found, notes = pipeline.discover(cfg)
    counts = pipeline.ingest(t, profile, found)
    for note in notes:
        say(f"  note: {note}")
    say(f"Checked {len(found)} listing(s): {counts['new']} new, {counts['auto_skipped']} auto-skipped by your rules, "
        f"{counts['needs_description']} need a description, {counts['duplicate']} already seen.")
    t.log(f"discover: {counts}", level="run")
    return 0


def cmd_ingest_inbox(args) -> int:
    from career import pipeline, sources

    profile, t = _profile(), tracker.Tracker()
    found, notes = sources.read_inbox(ROOT / "inbox")
    from career.gmail import GmailError, fetch_alert_messages, fetch_alerts, imap_configured
    if args.gmail or imap_configured():
        try:
            for raw in (fetch_alerts() if imap_configured() else fetch_alert_messages()):
                found += sources.parse_alert_eml(raw)
        except GmailError as exc:
            notes.append(f"Gmail: {exc}")
    counts = pipeline.ingest(t, profile, found)
    for note in notes:
        say(f"  note: {note}")
    say(f"Alerts and Telegram: {len(found)} job link(s): {counts['new']} new, {counts['needs_description']} need "
        f"their description pasted, {counts['auto_skipped']} skipped by your rules, {counts['duplicate']} already "
        "seen.")
    t.log(f"ingest-inbox: {counts}", level="run")
    return 0


def cmd_attach(args) -> int:
    from career import pipeline

    text = Path(args.file).read_text(encoding="utf-8") if args.file else _read_pasted()
    row = pipeline.attach_description(tracker.Tracker(), _profile(), args.job_id, text)
    say(f"{row['id']}: description saved. Keyword {row['keyword_score']}%, fit {row['fit']}, status {row['status']}.")
    return 0


def cmd_applied(args) -> int:
    changes = {"status": "completed-manual"}
    if args.date:
        changes["date_applied"] = args.date
    row = tracker.Tracker().update(args.job_id, **changes)
    say(f"{row['id']} marked applied on {row['date_applied']}.")
    return 0


def cmd_run(args) -> int:
    from career import agent

    t = tracker.Tracker()
    if args.mode:
        t.set_setting("apply_mode", args.mode)
    agent.run_once(t, _profile(), say)
    return 0


def cmd_revise(args) -> int:
    from career.packet import revise_packet

    instruction = " ".join(args.instruction) or _ask("What should change in the resume?", None)
    return revise_packet(tracker.Tracker(), _profile(), args.job_id, instruction, say=say)


def cmd_sync(args) -> int:
    from career import sync

    root = tracker.Tracker().root
    state = Path(args.state) if args.state else root / sync.STATE_DIR
    try:
        if args.no_git:
            counts = sync.sync_folders(root, state, personal_from_state=args.from_state)
        else:
            counts = sync.sync_with_remote(root, state, personal_from_state=args.from_state, say=say)
    except sync.SyncError as exc:
        say(f"Sync failed: {exc}")
        return 2
    say(f"Synced with the cloud: {counts['new']} new job(s), {counts['updated']} updated"
        + (f", {counts['renumbered']} renumbered" if counts["renumbered"] else "") + ".")
    return 0


def cmd_cloud_setup(args) -> int:
    import subprocess

    from career import sync

    root = tracker.Tracker().root
    state = root / sync.STATE_DIR
    if not (state / ".git").exists():
        url = args.repo if args.repo.startswith("https://") else f"https://github.com/{args.repo}.git"
        result = subprocess.run(["git", "clone", url, str(state)], capture_output=True, text=True)
        if result.returncode != 0:
            say(f"Could not clone {url}: {result.stderr.strip()[:300]}")
            return 2
    workflow = state / ".github" / "workflows" / "cloud-run.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "cloud" / "cloud-run.yml", workflow)
    (state / ".gitignore").write_text("*.db-wal\n*.db-shm\n*.tmp\n", encoding="utf-8")
    say("State repo ready in .state/. Syncing your data and profile to it...")
    return cmd_sync(argparse.Namespace(state=None, from_state=False, no_git=False))


def cmd_telegram_setup(args) -> int:
    from career import telegrambot

    try:
        say(telegrambot.setup(tracker.Tracker()))
    except telegrambot.BotError as exc:
        say(str(exc))
        return 1
    say("Forward job posts to your bot; every run picks them up (Telegram keeps them for 24 hours).")
    return 0


def cmd_fetch_descriptions(args) -> int:
    from career import jdfetch

    counts = jdfetch.fill_missing(tracker.Tracker(), _profile(), say=say)
    say(f"{counts['found']} description(s) found; {counts['not_found']} still need you to paste them "
        "(LinkedIn/Naukri links and pages the agent may not read).")
    return 0


def cmd_rescore(args) -> int:
    from career import pipeline

    counts = pipeline.rescore(tracker.Tracker(), _profile())
    say(f"Re-checked {counts['rescored']} job(s) against your current rules; "
        f"{counts['now_skipped']} now break a rule and were skipped. Pay updated for every job.")
    return 0


def cmd_apply_now(args) -> int:
    from career import agent

    outcome = agent.apply_now(tracker.Tracker(), _profile(), args.job_id, say)
    say(f"{args.job_id.upper()}: {outcome}")
    return 0 if outcome not in ("no-packet", "unavailable") else 2


def cmd_auto_apply(args) -> int:
    from career import autoapply

    try:
        result = autoapply.apply_to_job(tracker.Tracker(), _profile(), args.job_id,
                                        mode="takeover" if args.takeover else "auto", say=say)
    except autoapply.Unavailable as exc:
        say(str(exc))
        return 2
    say(f"{args.job_id.upper()}: {result.outcome}{' - ' + result.reason if result.reason else ''}")
    return 0 if result.outcome in ("submitted", "completed-manual") else 3


def cmd_ui(args) -> int:
    from ui.app import create_app

    say(f"Dashboard: http://127.0.0.1:{args.port}   (Ctrl+C to stop)")
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobs.py", description=textwrap.dedent(__doc__),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="first-time setup: create your profile and config files from the examples")

    add = sub.add_parser("add", help="save a job description")
    add.add_argument("--url", default="")
    add.add_argument("--company")
    add.add_argument("--role")
    add.add_argument("--location")
    add.add_argument("--file", help="read the description from a text file instead of pasting")
    add.add_argument("--apply", action="store_true", help="then tailor the resume and apply (follows apply_mode)")
    an = sub.add_parser("apply-now", help="tailor the resume for one job and apply (follows apply_mode)")
    an.add_argument("job_id")
    rv = sub.add_parser("revise", help="tell the agent what to change in a tailored resume; it re-scores it")
    rv.add_argument("job_id")
    rv.add_argument("instruction", nargs="*", help='e.g. "put the cloud project first and lead with Azure AD"')
    sy = sub.add_parser("sync", help="share jobs, resumes and your profile with the cloud run (private repo)")
    sy.add_argument("--state", help="the state repo folder (default: .state)")
    sy.add_argument("--from-state", action="store_true", help="take personal files from the state (the cloud run)")
    sy.add_argument("--no-git", action="store_true", help="merge folders only; the caller commits and pushes")
    cs = sub.add_parser("cloud-setup", help="connect this computer to your private state repo (once)")
    cs.add_argument("repo", help="OWNER/NAME of the private repo, e.g. you/career-agent-state")
    sub.add_parser("fetch-descriptions", help="fetch full descriptions for jobs that came with only a title")
    sub.add_parser("telegram-setup", help="connect your own Telegram bot (send it /start first)")
    sub.add_parser("rescore", help="re-check saved jobs against your current rules (after editing preferences)")

    lst = sub.add_parser("list", help="show the tracker")
    lst.add_argument("--status", choices=tracker.STATUSES + list(tracker.LEGACY))

    show = sub.add_parser("show", help="details for one job")
    show.add_argument("job_id")

    st = sub.add_parser("status", help="update a job's status")
    st.add_argument("job_id")
    st.add_argument("status", choices=tracker.STATUSES + list(tracker.LEGACY))
    st.add_argument("--note", default="")

    sub.add_parser("gaps", help="learning-gap summary")
    base = sub.add_parser("base", help="render the two base resumes")
    base.add_argument("--international", action="store_true", help="drop school-level education")

    ap = sub.add_parser("apply", help="tailored resume + report + form answers")
    ap.add_argument("job_id")
    ap.add_argument("--track", help="one of the track names in your profile (default: the best match)")
    ap.add_argument("--offline", action="store_true", help="no Gemini: base resume + answers only")
    ap.add_argument("--international", action="store_true")

    dg = sub.add_parser("digest", help="what to do next")
    dg.add_argument("--limit", type=int, default=15)
    sub.add_parser("check-key", help="test the Gemini API key")

    sub.add_parser("discover", help="check configured job boards and career pages (free)")
    ib = sub.add_parser("ingest-inbox", help="read job-alert emails from inbox/ (and Gmail with --gmail)")
    ib.add_argument("--gmail", action="store_true", help="also read the Gmail label (needs setup)")
    at = sub.add_parser("attach", help="add the full description to a listing")
    at.add_argument("job_id")
    at.add_argument("--file")
    apd = sub.add_parser("applied", help="mark a job as applied (today, or --date)")
    apd.add_argument("job_id")
    apd.add_argument("--date", help="YYYY-MM-DD")
    rn = sub.add_parser("run", help="one full pass: find jobs, tailor resumes, apply, hand off the rest")
    rn.add_argument("--mode", choices=["off", "review", "auto"], help="set apply_mode before running")
    for name, takeover in (("auto-apply", False), ("takeover", True)):
        aa = sub.add_parser(name, help="fill the application form in a browser" +
                            (" and leave Submit to you" if takeover else " and submit if everything is answered"))
        aa.add_argument("job_id")
        aa.set_defaults(takeover=takeover)
    ui = sub.add_parser("ui", help="open the dashboard")
    ui.add_argument("--port", type=int, default=5055)
    return parser


COMMANDS = {"init": cmd_init, "run": cmd_run, "apply-now": cmd_apply_now, "revise": cmd_revise,
            "rescore": cmd_rescore, "fetch-descriptions": cmd_fetch_descriptions, "telegram-setup": cmd_telegram_setup,
            "sync": cmd_sync, "cloud-setup": cmd_cloud_setup,
            "auto-apply": cmd_auto_apply, "takeover": cmd_auto_apply,
            "discover": cmd_discover, "ingest-inbox": cmd_ingest_inbox, "attach": cmd_attach,
            "applied": cmd_applied, "ui": cmd_ui, "add": cmd_add, "list": cmd_list, "show": cmd_show,
            "status": cmd_status, "gaps": cmd_gaps,
            "base": cmd_base, "apply": cmd_apply, "digest": cmd_digest, "check-key": cmd_check_key}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    if not (sys.argv[1:] if argv is None else argv):
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        say(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
