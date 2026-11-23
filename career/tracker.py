"""Job tracker (SQLite), event log, notifications, settings and learning-gap summary. All local and free.

One file, data/agent.db, holds everything. SQLite is used instead of the old CSV so the dashboard,
a scheduled run and the browser automation can all write at the same time without losing rows.
An old data/jobs.csv is imported automatically the first time the database is created.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from career import fit as fitmod
from career import taxonomy
from career.keywords import match
from career.profile import ROOT, Profile

# Life of a job, in order. "applied-auto" and "completed-manual" both mean the application was submitted.
STATUSES = ["discovered", "needs-description", "packet-ready", "awaiting-human", "applied-auto",
            "completed-manual", "interview", "offer", "rejected", "skipped", "withdrawn"]
SUBMITTED = ("applied-auto", "completed-manual")
# Names used by earlier versions (and still accepted on the command line).
LEGACY = {"found": "discovered", "new": "discovered", "analyzed": "packet-ready", "applied": "completed-manual"}
LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:[^#]*?[?&]currentJobId=|jobs/view/(?:[^/?#]*-)?)(\d{6,})", re.I)
NAUKRI_JOB = re.compile(r"naukri\.com/job-listings-[^?#]*?-(\d{9,})", re.I)


def listing_key(url: str) -> str:
    """'linkedin:<job id>' / 'naukri:<job id>' for a job link on those sites, else ""."""
    if m := LINKEDIN_JOB.search(url or ""):
        return "linkedin:" + m.group(1)
    if m := NAUKRI_JOB.search(url or ""):
        return "naukri:" + m.group(1)
    return ""
FIELDS = ["id", "added", "updated", "company", "role", "location", "url", "apply_url", "track", "keyword_score",
          "ats_score", "fit", "status", "output_dir", "notes", "source", "date_applied", "ats_before",
          "ats_after", "shortlist_pct", "dedupe_key", "handoff_reason", "apply_method", "attempts", "salary",
          "market_pay"]

# Settings shown on the dashboard's Settings page. Values are stored as text.
DEFAULT_SETTINGS = {
    "apply_mode": "review",          # off | review (you press Submit) | auto (agent submits)
    "min_keyword_score": "45",       # only jobs at or above this exact-keyword match are processed automatically
    "tailor_fits": "APPLY+REVIEW",   # fits that get a resume automatically: APPLY, or APPLY+REVIEW
    "ats_target": "85",              # below this ATS estimate, one more Gemini pass tries to raise it (0 = off)
    "market_pay": "on",              # on = estimate market pay per job (one Gemini call) for the salary answer
    "max_packets_per_run": "8",      # Gemini calls per run (free tier has daily limits)
    "gemini_gap_seconds": "12",      # pause between Gemini calls
    "max_applications_per_day": "10",
    "use_gemini": "on",              # off = base resume only
    "tick_consent_boxes": "off",     # on = tick required privacy/consent checkboxes for you
    "headless": "off",               # on = hide the browser window during automatic runs
    "handoff_wait_minutes": "20",    # how long the browser stays open when you take over
}

# Suggested free ways to close common gaps (learning only, never added to the resume).
LEARN = {
    "SIEM": "TryHackMe SOC Level 1 path; Splunk's free Splunk Fundamentals courses",
    "Splunk": "Splunk's free Splunk Fundamentals courses + Boss of the SOC datasets",
    "Microsoft Sentinel": "Microsoft Learn SC-200 learning path (free)",
    "Incident response": "TryHackMe SOC Level 1; NIST SP 800-61 incident handling guide",
    "SOC": "TryHackMe SOC Level 1 path",
    "Linux": "OverTheWire Bandit; Linux Journey",
    "Bash": "OverTheWire Bandit; write small scripts for your own tasks",
    "Docker": "Docker's official Get Started guide; containerize IaC Drift Guard",
    "Kubernetes": "kubernetes.io Basics tutorial; Killercoda free labs",
    "Networking (TCP/IP)": "Professor Messer Network+ videos (free); Cisco Packet Tracer",
    "Okta": "Okta free developer account + Okta Learning workforce identity basics",
    "SailPoint": "SailPoint IdentityNow docs / free webinars; concepts map to your Saviynt work",
    "CyberArk": "CyberArk's free PAM fundamentals training",
    "Privileged Access Management": "CyberArk free PAM fundamentals; relate to your Azure PIM work",
    "SAML/OAuth": "Okta developer docs on SAML and OAuth 2.0 / OIDC",
    "ISO 27001": "Free ISO 27001 introduction courses; map controls to your IAM work",
    "NIST": "NIST CSF 2.0 quick-start guides (free)",
    "Compliance": "NIST CSF + ISO 27001 overviews",
    "Agile": "Atlassian Agile Coach (free)",
    "PostgreSQL": "PostgreSQL official tutorial; add a database to a side project",
    "Express": "Express.js official guide; build a small REST API",
    "MITRE ATT&CK": "MITRE ATT&CK website + TryHackMe MITRE room",
    "Cloud security": "AWS Skill Builder free security courses",
    "AWS": "AWS Skill Builder free Cloud Practitioner learning plan",
    "GCP": "Google Cloud Skills Boost free labs",
    "SC-300": "Microsoft Learn SC-300 path (free); matches your Azure AD/PIM work",
}


SETTINGS_STAMP = "_settings_updated"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def slug(text: str, limit: int = 30) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit] or "job"


def normal_status(status: str) -> str:
    return LEGACY.get(status, status)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs ({", ".join(f"{f} TEXT NOT NULL DEFAULT ''" for f in FIELDS if f != "id")},
    id TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS jobs_key ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, job_id TEXT DEFAULT '',
    level TEXT DEFAULT 'info', message TEXT);
CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, job_id TEXT DEFAULT '',
    kind TEXT, message TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


class Tracker:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.db_path = self.root / "data" / "agent.db"
        self.csv_path = self.root / "data" / "jobs.csv"  # legacy, imported once
        self.jd_dir = self.root / "data" / "jd"
        self.gap_path = self.root / "learning" / "gap_summary.md"
        for d in (self.jd_dir, self.gap_path.parent):
            d.mkdir(parents=True, exist_ok=True)
        fresh = not self.db_path.exists()
        with self._db() as db:
            db.executescript(SCHEMA)
            have = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
            for column in FIELDS:  # databases from an older version gain new columns here
                if column not in have:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if fresh:
            self._import_legacy()

    # ---- storage ------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _import_legacy(self) -> None:
        if not self.csv_path.exists():
            return
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            old = list(csv.DictReader(fh))
        with self._db() as db:
            for row in old:
                row["status"] = normal_status(row.get("status", ""))
                values = [row.get(f, "") or "" for f in FIELDS]
                db.execute(f"INSERT OR IGNORE INTO jobs ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                           values)
        legacy_log = self.root / "data" / "activity.log"
        if legacy_log.exists():
            with self._db() as db:
                for line in legacy_log.read_text(encoding="utf-8").splitlines():
                    db.execute("INSERT INTO events (ts, message) VALUES (?, ?)", (line[:16], line[18:]))
        self.log(f"Imported {len(old)} job(s) from the old jobs.csv into agent.db")

    def rows(self, status: str = "") -> list[dict[str, str]]:
        sql, args = "SELECT * FROM jobs", ()
        if status:
            sql, args = sql + " WHERE status = ?", (normal_status(status),)
        with self._db() as db:
            return [{f: r[f] for f in FIELDS} for r in db.execute(sql + " ORDER BY id", args)]

    # ---- events, notifications, settings -------------------------------------
    def log(self, message: str, job_id: str = "", level: str = "info") -> None:
        with self._db() as db:
            db.execute("INSERT INTO events (ts, job_id, level, message) VALUES (?, ?, ?, ?)",
                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), job_id, level, message))

    def events(self, limit: int = 200, job_id: str = "", level: str = "") -> list[dict[str, str]]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if job_id:
            sql, args = sql + " AND job_id = ?", args + [job_id]
        if level:
            sql, args = sql + " AND level = ?", args + [level]
        with self._db() as db:
            return [dict(r) for r in db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def notify(self, kind: str, message: str, job_id: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO notifications (ts, job_id, kind, message) VALUES (?, ?, ?, ?)",
                       (_now(), job_id, kind, message))
        self.log(f"NOTIFY [{kind}] {message}", job_id, "warn")

    def notifications(self, include_done: bool = False) -> list[dict[str, str]]:
        sql = "SELECT * FROM notifications" + ("" if include_done else " WHERE done = 0") + " ORDER BY id DESC"
        with self._db() as db:
            return [dict(r) for r in db.execute(sql)]

    def clear_notifications(self, job_id: str = "", note_id: int | None = None) -> None:
        with self._db() as db:
            if note_id is not None:
                db.execute("UPDATE notifications SET done = 1 WHERE id = ?", (note_id,))
            elif job_id:
                db.execute("UPDATE notifications SET done = 1 WHERE job_id = ?", (job_id,))
            else:
                db.execute("UPDATE notifications SET done = 1")

    def settings(self) -> dict[str, str]:
        with self._db() as db:
            stored = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
        return {**DEFAULT_SETTINGS, **stored}

    def setting(self, key: str) -> str:
        return self.settings().get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # cloud sync: the newer set of settings wins
        with self._db() as db:
            for k, v in ((key, str(value)), (SETTINGS_STAMP, stamp)):
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))

    # ---- lookups ---------------------------------------------------------------
    def get(self, job_id: str) -> dict[str, str]:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ? COLLATE NOCASE", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"No job with id {job_id}. Run `python jobs.py list`.")
        return {f: row[f] for f in FIELDS}

    def jd_text(self, job_id: str) -> str:
        return (self.jd_dir / f"{job_id}.txt").read_text(encoding="utf-8")

    def find_key(self, key: str) -> dict[str, str] | None:
        if not key:
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
        return {f: row[f] for f in FIELDS} if row else None

    def write_jd(self, job_id: str, text: str) -> None:
        (self.jd_dir / f"{job_id}.txt").write_text(text, encoding="utf-8")

    def find_url(self, url: str) -> dict[str, str] | None:
        url = (url or "").strip().rstrip("/")
        if not url:
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE rtrim(trim(url), '/') = ?", (url,)).fetchone()
        return {f: row[f] for f in FIELDS} if row else None

    def find_listing(self, url: str) -> dict[str, str] | None:
        """find_url, and also the same LinkedIn/Naukri job under another link (tracking parameters, a
        search page with ?currentJobId=...), so the bookmark adds a description to the job saved before."""
        if row := self.find_url(url):
            return row
        key = listing_key(url)
        if not key:
            return None
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE url LIKE ?", (f"%{key.split(':')[1]}%",)).fetchall()
        return next(({f: r[f] for f in FIELDS} for r in rows if listing_key(r["url"]) == key), None)

    def submitted_today(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._db() as db:
            return db.execute("SELECT count(*) FROM jobs WHERE date_applied = ? AND status IN (?, ?)",
                              (today, *SUBMITTED)).fetchone()[0]

    # ---- operations ---------------------------------------------------------
    def add(self, *, company: str, role: str, url: str, location: str, text: str,
            track: str, keyword_score: int, fit: str, notes: str = "", status: str = "discovered",
            source: str = "manual", dedupe_key: str = "", apply_url: str = "") -> dict[str, str]:
        status = normal_status(status)
        with self._db() as db:  # one transaction: two writers can never get the same id
            db.execute("BEGIN IMMEDIATE")
            last = db.execute("SELECT max(CAST(substr(id, 2) AS INTEGER)) FROM jobs").fetchone()[0] or 0
            job_id = f"J{last + 1:04d}"
            row = {f: "" for f in FIELDS}
            row.update({"id": job_id, "added": _now(), "updated": _now(), "company": company, "role": role,
                        "location": location, "url": url, "apply_url": apply_url, "track": track,
                        "keyword_score": str(keyword_score), "fit": fit, "status": status, "notes": notes,
                        "source": source, "attempts": "0", "salary": fitmod.stated_pay(text),
                        "dedupe_key": dedupe_key or url.split("?")[0].rstrip("/").lower()})
            db.execute(f"INSERT INTO jobs ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                       [row[f] for f in FIELDS])
        (self.jd_dir / f"{job_id}.txt").write_text(text, encoding="utf-8")
        self.log(f"added from {source}: {role} @ {company} (keyword {keyword_score}%, fit {fit or '-'}, {status})",
                 job_id)
        return row

    def update(self, job_id: str, **changes: str) -> dict[str, str]:
        if "status" in changes:
            changes["status"] = normal_status(changes["status"])
            if changes["status"] not in STATUSES:
                raise ValueError(f"Status must be one of: {', '.join(STATUSES)}")
        unknown = set(changes) - set(FIELDS)
        if unknown:
            raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        row = self.get(job_id)
        before = row["status"]
        row.update({k: str(v) for k, v in changes.items()})
        if row["status"] in SUBMITTED and not row["date_applied"]:
            row["date_applied"] = datetime.now().strftime("%Y-%m-%d")
        if row["status"] != "awaiting-human" and "handoff_reason" not in changes:
            row["handoff_reason"] = ""
        row["updated"] = _now()
        with self._db() as db:
            db.execute(f"UPDATE jobs SET {', '.join(f'{f} = ?' for f in FIELDS if f != 'id')} WHERE id = ?",
                       [row[f] for f in FIELDS if f != "id"] + [row["id"]])
        if row["status"] != before:
            note = f" ({changes['notes']})" if changes.get("notes") else ""
            self.log(f"status {before} -> {row['status']}{note}", row["id"])
            if before == "awaiting-human":
                self.clear_notifications(job_id=row["id"])
        return row

    # ---- learning gaps ------------------------------------------------------
    def gap_report(self, profile: Profile) -> dict:
        """Skills saved jobs ask for that you lack: {"total", "partial": [...], "missing": [...]}.

        Each skill is {"skill", "count", "share", "area", "learn"}, most-asked first (25 per list).
        "partial" = known from coursework only; "missing" = not in baseline.yaml at all.
        """
        rows = [r for r in self.rows() if r["status"] not in ("skipped", "withdrawn", "needs-description")]
        missing, partial = Counter(), Counter()
        for row in rows:
            path = self.jd_dir / f"{row['id']}.txt"
            if not path.exists():
                continue
            result = match(profile, path.read_text(encoding="utf-8"))
            missing.update(result.missing)
            partial.update(result.partial)
        total = len(rows)

        def listed(counter: Counter) -> list[dict]:
            return [{"skill": term, "count": count, "share": round(100 * count / total),
                     "area": taxonomy.category(term), "learn": LEARN.get(term, "")}
                    for term, count in counter.most_common(25)]
        return {"total": total, "generated": _now(), "partial": listed(partial), "missing": listed(missing)}

    def gap_summary(self, profile: Profile) -> str:
        report = self.gap_report(profile)
        total = report["total"]
        lines = ["# Learning-gap summary", "",
                 f"_Generated {report['generated']} from {total} saved job(s). These are things to LEARN; "
                 "they are never added to your resume until baseline.yaml says you have them._", ""]
        if not total:
            lines.append("No jobs saved yet. Add some with `python jobs.py add`.")
        for title, skills in (
            ("You know it from coursework: get hands-on, then upgrade it in baseline.yaml", report["partial"]),
            ("Missing entirely", report["missing"]),
        ):
            if not skills:
                continue
            lines += [f"## {title}", "", "| Skill | Jobs asking | Share | Area | How to learn (free) |",
                      "|---|---|---|---|---|"]
            for s in skills:
                lines.append(f"| {s['skill']} | {s['count']} | {s['share']}% | {s['area']} | {s['learn'] or '-'} |")
            lines.append("")
        lines += ["## How to use this",
                  "1. Learn the top 2-3 items, building something real with each.",
                  "2. Put what you built on GitHub.",
                  "3. Update the skill's level in `profile/baseline.yaml` (academic -> project). "
                  "Only then will resumes include it.", ""]
        text = "\n".join(lines)
        self.gap_path.write_text(text, encoding="utf-8")
        return text
