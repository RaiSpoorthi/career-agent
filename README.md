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
2. **Tailors your resume** with Gemini for each job your rules mark APPLY, inside the truthfulness guard.
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

### LinkedIn and Naukri job alerts (from your email)
The program never visits LinkedIn or Naukri; it only reads the alert emails you receive.

**Simple way:** in Gmail, open an alert email, use **⋮ → Download message** to save a `.eml` file, and put it
in the `inbox/` folder. Then click **Read alert emails** (or `python jobs.py ingest-inbox`).

**Automatic way (Gmail, read-only, one-time setup):**
1. In Gmail, create a filter for alert emails that applies the label **job-alerts**.
2. At console.cloud.google.com create a project, enable the **Gmail API**, set up an **External** OAuth consent
   screen with your own email as a test user, and create an **OAuth client ID** of type **Desktop app**.
   Save the downloaded JSON in this folder as `client_secret.json`.
3. Run:
   ```bash
   pip install google-api-python-client google-auth-oauthlib
   python jobs.py ingest-inbox --gmail
   ```
   A browser window asks you to allow **read-only** access. After that it runs without asking.

Alert emails contain only the job title and link. Those jobs show under **Need the job description**: paste the
description on the job page (or `python jobs.py attach J0005`) and it is scored.

### Run the agent every morning (Windows Task Scheduler)
Create a Basic Task, trigger **Daily**, action **Start a program**: program `<folder>\.venv\Scripts\python.exe`,
arguments `jobs.py run`, start in `<folder>`. Each run tailors at most the number of resumes set on the Settings
page (8 by default), so the free Gemini limit isn't used up.

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
ui/app.py                     dashboard (python jobs.py ui)
profile/baseline.example.yaml example profile (a made-up person); yours is profile/baseline.yaml
config/*.example.yaml         example job sources and form answers; yours are config/sources.yaml, answers.yaml
inbox/                        drop job-alert .eml files here
career/
  profile.py                  loads your profile; finds your files or the examples
  keywords.py  fit.py         free keyword score and rule checks
  analyze.py   llm.py         tailoring with Gemini
  guard.py                    truthfulness guard
  resume.py  render.py        resume model, PDF + Word output, one-page fitting
  forms.py  answers.py        application-form answers
  tracker.py                  SQLite tracker (data/agent.db): jobs, log, notifications, settings
  agent.py                    the loop: find -> tailor -> apply -> hand off; and apply-now for one job
  packet.py                   builds the resume + report + answers for one job
  autoapply.py                fills and submits forms in a browser; hands off when unsure
  sources.py  pipeline.py     job boards, career pages, alert emails; de-duplicate and score
  gmail.py                    optional read-only Gmail reader
  taxonomy.py                 skills dictionary used for matching
tests/                        fake Gemini and practice forms on localhost; no real site is contacted
```

## Using it with Claude Code

`CLAUDE.md` holds the project rules for Claude Code (truthfulness, never automating account sites, and so on).
Put personal notes in `CLAUDE.local.md`; it is git-ignored and Claude Code reads it too.
