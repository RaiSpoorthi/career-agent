# Career Agent

A free, local job-search assistant with a dashboard. Clone it, describe yourself in one file, and it:

1. **Finds jobs** on public company job boards (Greenhouse, Lever, Ashby, Recruitee), on career pages you
   choose, in your LinkedIn and Naukri **job-alert emails**, and from any job you paste in yourself.
2. **Filters them** against your rules: location, visa sponsorship, salary floor, seniority.
3. **Tailors a one-page resume** for each good match with Google Gemini (free tier). You get a PDF and a Word
   file, pre-written form answers, and a report with ATS scores and a shortlist estimate.
4. **Fills in the application form** in a browser. By default you check it and press Submit yourself.
5. **Tracks everything** in a dashboard, and **tells you what to learn** from the skills jobs keep asking for.

**Truthfulness is enforced by code, not trust.** `profile/baseline.yaml` is the only source of facts.
Every bullet Gemini writes is checked, and it is reverted to your original wording if it adds a number,
tool, skill or seniority claim (like "led") that your profile doesn't support. Coursework-only skills
can only appear under "Familiar with (academic)". Each job's `report.md` lists every change the guard made.

**Cost: free.** Finding, filtering and tracking run on your computer with no AI. Gemini's free tier is used
only to tailor resumes, a few per run.

**Your details stay on your computer.** Your profile, form answers and job sources are git-ignored. The repo
ships only examples for a made-up person.

---

## Setup (about 15 minutes)

You need **Python 3.10 or newer** (on Windows, tick **"Add python.exe to PATH"** in the installer) and Git.

```bash
git clone https://github.com/RaiSpoorthi/career-agent.git
cd career-agent
python -m venv .venv
.venv\Scripts\activate            # Windows. macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # the free browser that fills in application forms
python jobs.py init                     # creates your own copies of the files below
```

`init` creates these files from the examples. They are git-ignored, so nothing you put in them is pushed:

| File | What to put in it |
|---|---|
| `.env` | Your free Gemini key from https://aistudio.google.com/apikey |
| `profile/baseline.yaml` | **You**: contact details, experience, projects, skills, education, resume tracks, job-search rules. Starts as a made-up person: replace everything, then delete the `sample: true` line. |
| `config/sources.yaml` | Companies and job boards to search, and the job titles and locations to keep |
| `config/answers.yaml` | Form answers only you know (current salary, relocation, and so on). Fill in only what is true. |

Then check it:

```bash
python jobs.py check-key        # tests the Gemini key
python jobs.py base             # writes your base resumes to output/base/ (one per track)
python -m pytest -q             # the tests use the example profile, never yours
python jobs.py ui               # dashboard at http://127.0.0.1:5055
```

Each time you open a new terminal, activate the environment first (`.venv\Scripts\activate`).

---

## Daily routine (about 15 minutes)

### Start the dashboard
**Windows:** double-click **`start-dashboard.bat`** in the project folder. A black window opens (leave it open)
and the dashboard opens in your browser at http://127.0.0.1:5055.

Or from a terminal in the project folder:
```bash
.venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
python jobs.py ui
```

**Stop it:** close that window, or press **Ctrl+C** in it.
**Restart it** (after updating the code, or if a page looks out of date): stop it, then start it again.
The dashboard only shows what's in `data/agent.db`, so stopping it never loses anything.

### Each day
1. **Find and tailor.** Press **Run agent now** on the dashboard (or let the daily scheduled run below do it).
   It finds new jobs, skips the ones that break your rules, and tailors a resume for the rest.
2. **Open Needs you.** Every job there has a tailored resume waiting. For each one:
   - Read the **Check first** reason, if any (for example "asks for 3+ years").
   - Open the job, check the **pay**, **fit** and **keyword match**, and read the tailored resume preview.
   - Not happy with the resume? Type what to change in **Change this resume** and press the button. Repeat
     until it reads right. Each change is checked for truthfulness and re-scored.
   - Press **Take over**. A browser opens with the form filled in and your resume attached. Check every answer,
     fill in anything left blank, and press **Submit**.
   - Press **I submitted it** (or **Skip** if you decided not to apply).
3. **Found a job yourself?** Use **Add job**: paste the link and description, and it is tailored and opened for you.

### Each week
- **Jobs:** update statuses (interview, offer, rejected) so the tracker stays useful.
- **Learn next:** pick one skill to build something real with. When you have, add it to your profile
  (`level: project`) so resumes can include it.
- Changed your rules in `profile/baseline.yaml`? Run `python jobs.py rescore` to re-check saved jobs.

### Run the agent automatically, twice a day (Windows)
Run this once in PowerShell, from the project folder. It runs at 7:00 and 18:00 (change the times if you like):
```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\pythonw.exe" -Argument "`"$dir\run_scheduled.pyw`"" -WorkingDirectory $dir
$triggers = @((New-ScheduledTaskTrigger -Daily -At 7:00AM), (New-ScheduledTaskTrigger -Daily -At 6:00PM))
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Career Agent" -Action $action -Trigger $triggers -Settings $settings
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1; powercfg /SETACTIVE SCHEME_CURRENT   # wake timers, plugged in only
```
- **Laptop asleep and plugged in:** Windows wakes it, the run happens with no window (the browser stays hidden),
  and it goes back to sleep on its usual timer. Use **Sleep**, not Shut down, at night.
- **Laptop shut down (or asleep on battery):** a run that was missed starts as soon as you turn it on.
- Each run: finds jobs (job boards, alert emails, Telegram exports in `inbox/`), fetches full descriptions,
  tailors up to the Settings limit, then applies (in `auto` mode, up to the daily cap). The home page shows
  **Since your last check**: what was submitted, and a button to the ones waiting for you.
- Everything the run printed is in `data/scheduled.log`. Check the task with `Get-ScheduledTask -TaskName "Career
  Agent"`; remove it with `Unregister-ScheduledTask -TaskName "Career Agent" -Confirm:$false`.

In `review` mode a scheduled run never submits anything: new resumes simply wait under **Needs you**. Each run
tailors at most the number of resumes set on the Settings page (8 by default), so the free Gemini limit isn't used up.

**Security codes.** Some forms (Greenhouse) email you a code after Submit. With Gmail connected (below), the agent
reads it, enters it and finishes the application. It reads only mail from application systems (Greenhouse, Lever,
Ashby, Recruitee) received in the last hour, read-only; nothing else in your inbox is looked at. Without Gmail the
application goes to **Needs you** with the reason.

### Run it in the cloud instead (computer can be off)
GitHub runs the agent for free at 7:00 and 18:00 India time (`cloud/cloud-run.yml`; a run takes about 10-20
minutes of the 2,000 free minutes a month that private repos get). Your jobs, resumes and profile live in a
**private** repo of yours, never in the public code repo.

1. On GitHub, create a **private** repo, e.g. `career-agent-state` (empty, no README).
2. In it, **Settings -> Secrets and variables -> Actions**: add the secrets `GEMINI_API_KEY`, `GMAIL_ADDRESS` and
   `GMAIL_APP_PASSWORD` (same values as your `.env`), and the variable `APP_REPO` = the public code repo
   (e.g. `you/career-agent`). With the GitHub CLI: `gh secret set GEMINI_API_KEY -R you/career-agent-state`.
3. On your computer: `python jobs.py cloud-setup you/career-agent-state`. It clones the repo into `.state/`,
   adds the workflow and pushes your current jobs and profile. Then commit and push the workflow:
   `git -C .state add -A; git -C .state commit -m "Cloud run"; git -C .state push`.
4. Turn off the local schedule so the two never apply at the same time:
   `Unregister-ScheduledTask -TaskName "Career Agent" -Confirm:$false`.
5. Test it: GitHub -> the state repo -> **Actions -> Career Agent (cloud run) -> Run workflow**.

How the two stay in step: the dashboard syncs when it starts and every 15 minutes (`python jobs.py sync` by hand).
Syncing merges job by job (the newer change wins), so marking a job submitted on your computer and a cloud run
finding new jobs never overwrite each other. Your profile and config go from your computer to the cloud only, so
keep editing them here. Captchas, security codes without Gmail, and portals that need an account still come to
**Needs you**: open the dashboard and finish them. The cloud browser runs from GitHub's servers, so some sites
show captchas more often than on your own computer.

### Where your data is
Everything the agent tracks is in one SQLite file, `data/agent.db` (tables: `jobs`, `events`, `notifications`,
`settings`). The dashboard is its viewer. To browse it like a spreadsheet, open it in the free
[DB Browser for SQLite](https://sqlitebrowser.org) (close it before a run; don't edit rows by hand while the agent
is running). Tailored resumes and reports are in `output/`.

---

## Writing your profile (`profile/baseline.yaml`)

Every resume is built from this file and nothing else. The example shows every field; the main parts:

- **`contact`**: name, email, phone, location (`City, Country`), LinkedIn, GitHub.
- **`experience`** and **`projects`**: each has an `id`, a `stack` (tools you really used there) and
  `bullets`, each bullet with its own `id`. A bullet may have `variants` (other wording of the same facts) per track.
- **`skills`**: every skill you may claim, with a `level`: `work`, `project` or `academic` (coursework only).
- **`tracks`**: one base resume per kind of job you apply for. Name them anything (`security`, `data`,
  `frontend`...). Each picks a headline, which experience, projects and bullets appear, and the skills layout.
  `categories` (from `career/taxonomy.py`: security, software, data, general) decides which jobs it is used for.
- **`education`**, **`certifications`**, **`achievements`**: certifications and achievements may have
  `tracks` (only most relevant to some resumes) and `priority` (a lower number is dropped first if a resume runs
  past one page).
- **`preferences`**: your job-search rules:
  - `home_country` (defaults to the country in your contact location) and `ok_locations` (places you'd work).
    Cities are built in for India. For other countries, list them in `home_places`.
    `other_home_locations`: `review` flags jobs in other cities of your country, `skip` leaves them out.
    Remote jobs limited to another country ("Remote - USA") are always skipped.
  - `salary_min` (annual minimum by currency: USD, GBP, EUR), `salary_min_lpa` (India: lakhs per annum) and
    `expected_salary` (your own answer for home-country jobs). All optional.
  - `max_years_required`, `needs_visa_sponsorship_abroad`, `notice_period_days`, `work_authorization`,
    `graduation_year`. **Check `work_authorization` is accurate**: it goes on application forms.

After editing, run `python -m pytest -q` and `python jobs.py base`. A clear error tells you if a track names a
skill or bullet that doesn't exist.

**Learned something real?** Change the skill's `level` from `academic` to `project` and add it to the `stack`
of the project where you used it. Only then can resumes mention it.

---

## Using it

### The agent in one minute

```bash
python jobs.py ui        # dashboard at http://127.0.0.1:5055
python jobs.py run       # one full pass (the dashboard's "Run agent now" button does the same)
```

One pass does three things:

1. **Finds jobs** on the boards in `config/sources.yaml` and in your alert emails, and scores each against your
   rules: **APPLY**, **REVIEW** (something to confirm first) or **SKIP** (breaks a hard rule).
2. **Tailors your resume** with Gemini for each job your rules mark APPLY or REVIEW (best first, up to the
   per-run limit), inside the truthfulness guard. REVIEW jobs wait under **Needs you** with the reason to check,
   and are never submitted for you. Set "Tailor resumes automatically for" to `APPLY` on Settings to skip them.
   Each run also re-checks jobs found earlier against your current rules.
3. **Applies**, according to the apply mode on the Settings page:
   - `review` (the default): nothing is sent. Each job waits under **Needs you**. Press **Take over** and a
     browser opens with the form filled in and your resume attached. You check it and press Submit.
   - `auto`: the agent submits the form itself when every required question is answered from your profile.
     Anything else is handed to you: a question it has no answer for, a captcha, a consent box you have not
     allowed it to tick, or a submission it could not confirm. Capped per day (10 by default).
   - `off`: stop after tailoring.

Start in `review`. Switch to `auto` once you have watched it fill a few real forms correctly.

### A job you found yourself

On the dashboard, open **Add job** (or **Apply to a job I found**), paste the link and the full description,
and press **Tailor resume and apply**. It is scored, a resume is tailored, and the form is opened (review mode)
or submitted (auto mode). The job page shows the fit, keyword match, ATS estimate, matched and missing skills,
and a preview of the tailored resume. From the terminal: `python jobs.py add --url <link> --apply`.

### Changing a tailored resume

On a job's page, type what you want in **Change this resume**, for example "put my internship first and start
the headline with Azure AD", and press the button. Gemini rewrites the resume your way, the truthfulness guard
checks every line again, and the ATS score is re-checked. You see what was done, what wasn't (anything your
profile can't support is refused, with the reason), and the score before and after. From the terminal:
`python jobs.py revise J0001 "put my internship first"`.

### Pay and your expected salary

Pay is read from each posting (LPA, USD, GBP or EUR) and shown in `python jobs.py list`, `show`, `digest`, and on
the dashboard. Most postings don't state it; those show "not stated".

When a resume is tailored, Gemini also estimates the **usual pay for that role at your level** (city, company type,
your experience). Your expected-salary answer on forms becomes the middle of that range, **never below your own
floor** (`salary_min_lpa` / `salary_min`), with "(open to discussion)". The estimate and its basis are shown on the
job page and in `answers.md`. Gemini's live web search (grounding) isn't included in the free tier, so the estimate
says "no web search" when it comes from Gemini's own knowledge. Turn it off on Settings ("Expected salary from market
pay").

### ATS scores

The ATS numbers are Gemini's estimates; real ATS tools differ. Two things raise them **honestly**:
- Every skill the job asks for that your profile backs (work/project level, or a tool in a job's or project's stack)
  is put in your Skills section; coursework-only ones go under "Familiar with". `report.md` lists what was added.
- If the estimate is below your **ATS target** (Settings, 85 by default), one more Gemini pass rewrites the resume
  to match the job more closely, and the better version is kept.

A skill you don't have is never added to reach a score; it goes to **Learn next** instead. If a job keeps scoring
low, that's usually a real gap.

### Jobs page

Tabs for **Open**, **Applied** (applied-on date, status, pay, ATS, resume, report and answers, listing link) and
**Skipped**, plus a search box (role, company, location, skill, status). Each job page starts with **At a glance**:
experience asked, pay, location, must-have and nice-to-have skills, what you'd do, and which skills you have or miss.
The full description is folded underneath.

### The dashboard

- **Dashboard:** counts, best new matches, packets ready, listings that still need a description, recent activity.
- **Needs you:** applications waiting for you, with **Take over**, the resume and the answers.
- **Jobs:** every job with source, fit, keyword %, ATS before → after, shortlist estimate, status and applied-on date.
- **Add job:** paste a job you found and apply to it.
- **Learn next:** skills to study, ranked by how many jobs ask for them, with free resources.
- **Logs** and **Settings** (apply mode, limits, and your sources and answers files).

ATS and shortlist numbers are Gemini's estimates, not real probabilities. Keyword % is an exact count
done on your computer.

### What each packet contains (`output/J0001_company_role/`)

| File | What it is |
|---|---|
| `resume.pdf` | Tailored one-page resume with clickable links. Upload this. |
| `resume.docx` | The same resume in Word |
| `answers.md` | Form answers: notice period, work authorization, sponsorship, expected salary, links, and a short checked "why I'm a fit" draft |
| `report.md` | ATS scores before and after, job analysis, gap analysis, guard log, recruiter review, what to learn |

If a resume can't fit on one page even after dropping less relevant items and shrinking the font, no PDF is
saved and `report.md` says why. If Gemini is unavailable, `python jobs.py apply J0001 --offline` still gives
you the best-fit base resume and the form answers.

### Commands

```
python jobs.py                      # list of every command
python jobs.py init                 # first-time setup
python jobs.py run [--mode auto]    # one full pass
python jobs.py add [--apply]        # save a job (paste its description); --apply also tailors and applies
python jobs.py apply-now J0001      # tailor (if needed) and apply to one saved job
python jobs.py revise J0001 "..."   # change a tailored resume your way; re-checks it and the ATS score
python jobs.py rescore              # re-check saved jobs after you change your rules
python jobs.py apply J0001 [--track NAME] [--offline] [--international]
python jobs.py takeover J0001       # open the filled form; you press Submit
python jobs.py digest               # what to apply to next
python jobs.py list [--status ...]  # all jobs
python jobs.py status J0001 interview --note "HR call Tue"
python jobs.py applied J0001        # mark applied today (or --date YYYY-MM-DD)
python jobs.py gaps                 # learning-gap summary
python jobs.py base [--international]
```

`--international` leaves out education entries marked `drop_for_international`.

---

## Finding jobs

### Company job boards and career pages
`config/sources.yaml` starts with a few companies on Greenhouse and Lever. Replace them with your own; the
format is in the file's comments.

- **Greenhouse / Lever / Ashby / Recruitee boards:** many companies list jobs at `job-boards.greenhouse.io/<name>`
  or `jobs.lever.co/<name>`. Put the `<name>` in the file. These are public feeds meant for this. A wrong name
  only logs a "note:" line for that company.
- **Other career pages:** add the URL and words to look for in link text. The program follows only links on the
  same site, checks `robots.txt` before every page, waits 2 seconds between pages, and reads at most 25 listings
  per company.

### Job alerts from your email (LinkedIn, Naukri and others)
The program never visits LinkedIn or Naukri; it only reads the alert emails you receive.

**Automatic, read-only (Gmail, 5 minutes):**
1. Turn on 2-Step Verification for your Google account, then create an **app password** at
   https://myaccount.google.com/apppasswords (name it "Career Agent").
2. In `.env` add:
   ```
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=the 16-letter app password
   ```
Every run (and **Read alert emails** on the dashboard) then reads the last 3 days of alert emails from LinkedIn,
Naukri, foundit, Instahyre, Wellfound, Cutshort, hirist and iimjobs. The mailbox is opened read-only: nothing is
marked read, moved or sent. Remove the two lines (or revoke the app password) to stop it.

**Without Gmail access:** open an alert email, use **⋮ → Download message** to save a `.eml` file into `inbox/`,
then click **Read alert emails** (or `python jobs.py ingest-inbox`).

Alert emails contain only the job title and link. Those jobs show under **Need the job description**: open the link
and use the **Save to Career Agent** bookmark (below), or paste the description on the job page.

### Telegram job groups
1. In **Telegram Desktop**, open the group, then **⋮ → Export chat history**. Untick photos and other media, set
   **Format: Machine-readable JSON** and a date range of the last week or two, and save it into this project's
   `inbox/` folder.
2. Click **Read alert emails** on the dashboard (or `python jobs.py ingest-inbox`, or wait for the next run).

Every job link posted in the last 14 days becomes a job (links to Telegram, WhatsApp and social sites are ignored).
Re-exporting the same group later is fine: jobs already seen are skipped. No Telegram login, bot or automation is used.

**Safety first.** Links posted by strangers are screened before anything opens them (`career/linksafety.py`).
Saved as skipped with the reason, and never opened by the agent: links that are not https, bare IP addresses,
look-alike domains (a fake "greenhouse" site), punycode, file downloads (.apk, .exe, .zip...), risky domain endings,
Google Forms and similar data-collection forms, posts that ask for a fee or deposit, and short links whose
destination can't be seen. Short links are followed with plain requests that only read where they lead.

**Descriptions.** Most posts are one line and a link, so the agent fetches the full description itself
(`python jobs.py fetch-descriptions`, also part of every run): from the public job data of Workday, Oracle,
Greenhouse, Lever and Ashby, or from the page's text when its robots.txt allows. LinkedIn and Naukri links are
never fetched; use the bookmark below for those. Jobs on company portals that need an account (Workday, Oracle...)
get a tailored resume and wait under **Needs you** with **Apply in my browser**; only public forms are submitted.

### Save to Career Agent: any job page, one click (LinkedIn included)
On the dashboard's **Add job** page, drag the **Save to Career Agent** button to your browser's bookmarks bar. When
you're viewing a job (on LinkedIn, Naukri, a company site...), click it: Add job opens with the link, title, company
and description filled in, ready for **Tailor resume and apply**. It only copies the page you're looking at when you
click it, like copy and paste. The agent never visits LinkedIn or Naukri itself, and those jobs are never auto-applied.

**Why there's no automatic LinkedIn scraping:** LinkedIn restricts accounts for automated reading as well as for
automated applying, and it's against their terms. Alerts plus this button give you the same jobs without that risk.

To run the whole agent every morning, see **Daily routine** above.

---

## Free-tier notes

- Google doesn't publish fixed free limits. Yours are shown in AI Studio under **Rate limit**. If a model hits
  its limit or is retired, the next model in `GEMINI_MODELS` (in `.env`) is used.
- Google's terms say free-tier requests may be used to improve their products. Job descriptions and your
  profile are sent to Gemini when a resume is tailored. Everything else stays on your computer.

## Privacy

- Git-ignored: `.env` (your key), `profile/baseline.yaml`, `config/answers.yaml`, `config/sources.yaml`,
  `client_secret.json` and `gmail_token.json`, `data/` (tracker, job descriptions, log), `output/`, `learning/`,
  and alert emails in `inbox/`. Run `git status` before pushing a fork to be sure.

## What it deliberately does not do

- **It never logs in to, scrapes or applies on LinkedIn, Naukri, Indeed or any site that needs an account.**
  Automation there is against their terms and is the main reason accounts get restricted.
- **It never solves or works around a captcha**, and never guesses an answer to a form question.
- **It doesn't add any claim your profile doesn't support, ever.**
- **It doesn't send in bulk.** Automatic submissions are capped per day.

## Project layout

```
jobs.py                       command line (python jobs.py lists every command)
start-dashboard.bat           double-click to start the dashboard (Windows)
ui/app.py                     dashboard (python jobs.py ui)
profile/baseline.example.yaml example profile (a made-up person); yours is profile/baseline.yaml
config/*.example.yaml         example job sources and form answers; yours are config/sources.yaml, answers.yaml
inbox/                        drop job-alert .eml files and Telegram chat exports (.json) here
career/
  profile.py                  loads your profile; finds your files or the examples
  keywords.py  fit.py         free keyword score and rule checks
  analyze.py   llm.py         tailoring with Gemini
  marketpay.py                usual pay for a role at your level (expected-salary answer)
  jdsummary.py                "At a glance" summary of a job description
  guard.py                    truthfulness guard
  resume.py  render.py        resume model, PDF + Word output, one-page fitting
  forms.py  answers.py        application-form answers
  tracker.py                  SQLite tracker (data/agent.db): jobs, log, notifications, settings
  agent.py                    the loop: find -> tailor -> apply -> hand off; and apply-now for one job
  packet.py                   builds the resume + report + answers for one job
  autoapply.py                fills and submits forms in a browser; hands off when unsure
  sources.py  pipeline.py     job boards, career pages, alert emails, Telegram exports; de-duplicate and score
  gmail.py                    optional read-only Gmail reader (app password or Google API)
  taxonomy.py                 skills dictionary used for matching
tests/                        fake Gemini and practice forms on localhost; no real site is contacted
```

## Using it with Claude Code

`CLAUDE.md` holds the project rules for Claude Code (truthfulness, never automating account sites, and so on).
Put personal notes in `CLAUDE.local.md`; it is git-ignored and Claude Code reads it too.
