# Career Agent: instructions for Claude Code

A personal job-application system that anyone can clone and fill with their own details. Read README.md for usage.
"The user" below is whoever runs this copy. Personal notes, if any, are in `CLAUDE.local.md` (not committed).

## Personal files vs committed files
- The user's details live in `profile/baseline.yaml`, `config/answers.yaml` and `config/sources.yaml`.
  They are git-ignored. Never commit them, and never copy anything from them into committed files
  (examples, tests, README, docs).
- Committed examples: `profile/baseline.example.yaml` (a made-up person, marked `sample: true`),
  `config/answers.example.yaml`, `config/sources.example.yaml`, `.env.example`. `python jobs.py init` copies them.
- Tests always use the examples (tests/conftest.py sets `CAREER_AGENT_EXAMPLES`), so they pass on any machine.

## Non-negotiable rules
1. **Absolute truthfulness.** Never add experience, projects, metrics, tools, skills or certifications
   to `profile/baseline.yaml` unless the user states them in this conversation. Never "improve" numbers.
2. Never weaken `career/guard.py` (the truthfulness guard) or its tests to make output look better.
3. Coursework-only skills stay `level: academic` until the user says they have built something real with them.
4. Never print, commit or move the key in `.env`. Never commit `data/`, `output/` or `learning/`.

## Job discovery and application rules (keep these)
- Never log in to, scrape or automate LinkedIn, Naukri, Indeed or any site that needs an account
  (`BLOCKED_HOSTS` in career/autoapply.py). Their alerts are read only from the user's own email. Never remove a
  host from that list or add stealth/anti-detection code.
- Auto-apply is allowed only on public, no-account application forms (`SUPPORTED_HOSTS`). It answers only
  from career/answers.py (baseline.yaml + config/answers.yaml). Never let an LLM or a default value answer
  a form question. A required question with no answer, a captcha, or an unconfirmed submission must end in
  `awaiting-human`. Never add captcha solving.
- Keep the daily submission cap and the "review" default for `apply_mode`.
- Career pages: robots.txt must allow it, same-site links only, 2-second delay, max 25 listings per company.
- Gemini runs only inside career/packet.py, capped per run by the `max_packets_per_run` setting.

## Architecture
- `career/profile.py`  loads the profile; `personal_file()` / `profile_path()` pick the user's file or the example.
- `career/tracker.py`  SQLite (data/agent.db): jobs, events (log), notifications, settings.
- `career/pipeline.py`, `career/sources.py`  discovery and offline scoring; `fit.py` applies `preferences`.
- `career/packet.py`  tailored resume + report + answers (guard runs inside analyze.run).
- `career/render.py`  PDF + Word, fitted to one page (drops off-track certifications, then low-priority achievements).
- `career/answers.py`, `career/autoapply.py`  form answers and the Playwright form filler.
- `career/agent.py`  the loop: discover -> tailor -> apply -> hand off (`python jobs.py run`), and
  `apply_now` for a single job the user adds (`python jobs.py add --apply`, dashboard "Add job").
- `ui/app.py`  Flask dashboard; long actions run as background `jobs.py` subprocesses.

## Working on the code
- Run `python -m pytest -q` and `ruff check .` after any change. Tests use a fake Gemini (tests/fakes.py) and
  practice forms served from localhost; no real job site is contacted.
- Nothing may assume one user: home country, cities, pay, track names and contact details come from
  `baseline.yaml`. Use the fictional example profile in tests.
- Dashboard: `python jobs.py ui` (Flask, 127.0.0.1 only). Its buttons call jobs.py commands.
- Gemini is only called in `career/llm.py`. Keep it on free-tier models by default.
- Resume output must stay single-column, one page, with real text and clickable links (ATS-friendly).
- Prefer free/offline solutions. Do not add paid services.

## Common requests
- "I learned X": ask what they built and where, then update its level/stack in baseline.yaml and run tests.
- "Add this job": `python jobs.py add --file <saved text>` (add `--apply` to tailor and apply), or the dashboard's Add job page.
- "Why was a bullet reverted?": see the "Truthfulness guard log" in that job's report.md.
