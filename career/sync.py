"""Share the agent's state between this computer and the cloud run (GitHub Actions), in a private repo.

The private "state" repo holds data/agent.db, data/jd/, output/ and your personal files (profile, config).
Both sides change the job database, so syncing never overwrites one copy with the other: jobs are merged
row by row (the more recently updated copy of each job wins), log lines and notifications are combined,
and the newer set of settings wins. A job both sides created under the same number keeps both: the
incoming one gets a new number.

    python jobs.py sync                    this computer: merge with the cloud's state and push yours
    python jobs.py sync --state DIR --from-state    (the cloud run, before and after it works)

Your personal files go from this computer to the cloud (you edit them here), never the other way.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from career.tracker import FIELDS, SETTINGS_STAMP, Tracker

STATE_DIR = ".state"  # this computer's clone of the private state repo (git-ignored)
PERSONAL = ("profile/baseline.yaml", "config/answers.yaml", "config/sources.yaml")


class SyncError(RuntimeError):
    pass


def _rows(db: sqlite3.Connection, sql: str) -> list[dict]:
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(sql)]


def _next_id(db: sqlite3.Connection) -> str:
    last = db.execute("SELECT max(CAST(substr(id, 2) AS INTEGER)) FROM jobs").fetchone()[0] or 0
    return f"J{last + 1:04d}"


def merge_db(local: Tracker, other_db: Path, other_root: Path) -> dict[str, int]:
    """Merge another copy of the database (and its job files) into `local`. Returns counts."""
    counts = {"new": 0, "updated": 0, "renumbered": 0}
    if not other_db.exists():
        return counts
    with closing(sqlite3.connect(other_db)) as src:
        theirs = _rows(src, "SELECT * FROM jobs")
        their_events = _rows(src, "SELECT ts, job_id, level, message FROM events")
        their_notes = _rows(src, "SELECT ts, job_id, kind, message, done FROM notifications")
        their_settings = {r["key"]: r["value"] for r in _rows(src, "SELECT key, value FROM settings")}
    renamed: dict[str, str] = {}
    with local._db() as db:
        mine = {r["id"]: r for r in _rows(db, "SELECT * FROM jobs")}
        by_key = {r["dedupe_key"]: r["id"] for r in mine.values() if r["dedupe_key"]}
        for row in theirs:
            row = {f: row.get(f, "") or "" for f in FIELDS}
            own_id = by_key.get(row["dedupe_key"]) if row["dedupe_key"] else None
            own_id = own_id or (row["id"] if row["id"] in mine and mine[row["id"]]["dedupe_key"] == row["dedupe_key"]
                                else None)
            src = {"id": row["id"], "output_dir": row["output_dir"]}  # where its files are on the other side
            if own_id:
                _renumber(row, own_id, renamed)
                if (row["updated"] or "") > (mine[own_id]["updated"] or ""):
                    _upsert(db, row)
                    _copy_job_files(other_root, local.root, src, row)
                    counts["updated"] += 1
                continue
            if row["id"] in mine:  # same number, different job: give the incoming one a new number
                _renumber(row, _next_id(db), renamed)
                counts["renumbered"] += 1
            _upsert(db, row)
            _copy_job_files(other_root, local.root, src, row)
            mine[row["id"]] = row
            counts["new"] += 1

        seen = {(e["ts"], e["job_id"], e["message"]) for e in _rows(db, "SELECT ts, job_id, message FROM events")}
        for e in their_events:
            e["job_id"] = renamed.get(e["job_id"], e["job_id"])
            if (e["ts"], e["job_id"], e["message"]) not in seen:
                db.execute("INSERT INTO events (ts, job_id, level, message) VALUES (?, ?, ?, ?)",
                           (e["ts"], e["job_id"], e["level"], e["message"]))
        have = {(n["ts"], n["job_id"], n["message"]): n["done"]
                for n in _rows(db, "SELECT ts, job_id, message, done FROM notifications")}
        for n in their_notes:
            n["job_id"] = renamed.get(n["job_id"], n["job_id"])
            key = (n["ts"], n["job_id"], n["message"])
            if key not in have:
                db.execute("INSERT INTO notifications (ts, job_id, kind, message, done) VALUES (?, ?, ?, ?, ?)",
                           (n["ts"], n["job_id"], n["kind"], n["message"], n["done"]))
            elif n["done"] and not have[key]:  # dismissed on either side stays dismissed
                db.execute("UPDATE notifications SET done = 1 WHERE ts = ? AND job_id = ? AND message = ?", key)

        my_settings = {r["key"]: r["value"] for r in _rows(db, "SELECT key, value FROM settings")}
        if their_settings.get(SETTINGS_STAMP, "") > my_settings.get(SETTINGS_STAMP, ""):
            for key, value in their_settings.items():
                if key != "ui_secret":  # each dashboard keeps its own
                    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    return counts


def _renumber(row: dict, new_id: str, renamed: dict[str, str]) -> None:
    if row["id"] == new_id:
        return
    renamed[row["id"]] = new_id
    if row["output_dir"]:
        row["output_dir"] = row["output_dir"].replace(row["id"], new_id, 1)
    row["id"] = new_id


def _upsert(db: sqlite3.Connection, row: dict) -> None:
    cols = [f for f in FIELDS]
    db.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols if c != 'id')}",
               [row[c] for c in cols])


def _copy_job_files(src_root: Path, dst_root: Path, src_row: dict, dst_row: dict) -> None:
    jd = src_root / "data" / "jd" / f"{src_row['id']}.txt"
    if jd.exists():
        (dst_root / "data" / "jd").mkdir(parents=True, exist_ok=True)
        shutil.copy2(jd, dst_root / "data" / "jd" / f"{dst_row['id']}.txt")
    if src_row.get("output_dir") and (src_root / src_row["output_dir"]).is_dir():
        shutil.copytree(src_root / src_row["output_dir"], dst_root / dst_row["output_dir"], dirs_exist_ok=True)


def _mirror_state(app: Path, state: Path) -> None:
    """Make the state folder an exact copy of this side's (already merged) database and job files."""
    (state / "data").mkdir(parents=True, exist_ok=True)
    tmp = state / "data" / "agent.db.tmp"
    with closing(sqlite3.connect(app / "data" / "agent.db")) as src, closing(sqlite3.connect(tmp)) as dst:
        src.backup(dst)  # a consistent copy even while the dashboard has the database open
    (state / "data" / "agent.db.tmp").replace(state / "data" / "agent.db")
    for sub in ("data/jd", "output"):
        if (app / sub).exists():
            shutil.copytree(app / sub, state / sub, dirs_exist_ok=True)


def _copy_personal(src: Path, dst: Path) -> None:
    for rel in PERSONAL:
        if (src / rel).exists():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst / rel)


def sync_folders(app: Path, state: Path, personal_from_state: bool) -> dict[str, int]:
    """Merge the state folder into the app's data, then write the merged result back to the state folder."""
    tracker = Tracker(app)
    counts = merge_db(tracker, state / "data" / "agent.db", state)
    if personal_from_state:
        _copy_personal(state, app)
    else:
        _copy_personal(app, state)
    _mirror_state(app, state)
    return counts


def _git(state: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(state), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()[:300]}")
    return result


def sync_with_remote(app: Path, state: Path, personal_from_state: bool = False, push: bool = True,
                     attempts: int = 3, say=print) -> dict[str, int]:
    """Pull the state repo, merge both ways, commit and push (retrying if the other side pushed meanwhile)."""
    if not (state / ".git").exists():
        raise SyncError(f"No state repo at {state}. Set it up first (README: 'Run it in the cloud').")
    for attempt in range(attempts):
        _git(state, "fetch", "origin")
        branch = _git(state, "symbolic-ref", "--short", "HEAD").stdout.strip()  # works in an empty repo too
        if _git(state, "rev-parse", "--verify", f"origin/{branch}", check=False).returncode == 0:
            _git(state, "reset", "--hard", f"origin/{branch}")
        counts = sync_folders(app, state, personal_from_state)
        _git(state, "add", "-A")
        if _git(state, "diff", "--cached", "--quiet", check=False).returncode != 0:
            _git(state, "-c", "user.name=career-agent", "-c", "user.email=career-agent@users.noreply.github.com",
                 "commit", "-q", "-m", "Sync agent state")
        if not push or not _ahead(state, branch):
            return counts
        pushed = _git(state, "push", "origin", f"HEAD:{branch}", check=False)
        if pushed.returncode == 0:
            return counts
        race = "fetch first" in pushed.stderr or "non-fast-forward" in pushed.stderr
        if not race:  # e.g. missing permission: say what GitHub said instead of retrying
            raise SyncError(f"GitHub refused the push: {pushed.stderr.strip()[-300:]}")
        say(f"  The other side pushed meanwhile; merging again ({attempt + 1}/{attempts})...")
    raise SyncError("Could not push the state after several tries. Run `python jobs.py sync` again.")


def _ahead(state: Path, branch: str) -> bool:
    """Does this clone have commits GitHub doesn't (or has nothing been pushed yet)?"""
    if _git(state, "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        return False  # nothing committed at all
    if _git(state, "rev-parse", "--verify", f"origin/{branch}", check=False).returncode != 0:
        return True
    return bool(_git(state, "rev-list", f"origin/{branch}..HEAD").stdout.strip())
