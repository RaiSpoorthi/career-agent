"""Laptop <-> cloud state sync: row-level merge, nothing overwritten wholesale, files follow their jobs."""

import subprocess
import time

import pytest

from career import sync
from career.tracker import Tracker


def add(t, n, **kw):
    base = dict(company="Acme", role=f"Engineer {n}", url=f"https://boards.greenhouse.io/acme/jobs/{n}",
                location="Bengaluru", text=f"Job {n} description", track="software", keyword_score=70, fit="APPLY")
    return t.add(**{**base, **kw})


def packet(t, job_id):
    folder = t.root / "output" / f"{job_id}_acme"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "resume.pdf").write_bytes(b"%PDF fake")
    t.update(job_id, output_dir=f"output/{job_id}_acme", status="packet-ready")


def test_newer_change_wins_per_job_and_new_jobs_arrive_with_their_files(tmp_path):
    laptop, cloud = Tracker(tmp_path / "laptop"), Tracker(tmp_path / "cloud")
    add(laptop, 1)
    sync.merge_db(cloud, laptop.db_path, laptop.root)  # the cloud starts from the laptop's state
    add(cloud, 2)
    packet(cloud, "J0002")
    cloud.log("submitted J0002", "J0002")
    laptop.update("J0001", status="completed-manual")  # you marked it on the laptop, later
    counts = sync.merge_db(laptop, cloud.db_path, cloud.root)
    assert counts == {"new": 1, "updated": 0, "renumbered": 0}
    assert laptop.get("J0001")["status"] == "completed-manual"  # your newer change is kept
    assert laptop.get("J0002")["status"] == "packet-ready"
    assert (laptop.root / "output" / "J0002_acme" / "resume.pdf").exists()
    assert (laptop.jd_dir / "J0002.txt").read_text(encoding="utf-8") == "Job 2 description"
    assert any(e["message"] == "submitted J0002" for e in laptop.events())
    sync.merge_db(laptop, cloud.db_path, cloud.root)  # merging again changes nothing
    assert len([e for e in laptop.events() if e["message"] == "submitted J0002"]) == 1


def test_same_number_for_different_jobs_keeps_both(tmp_path):
    laptop, cloud = Tracker(tmp_path / "laptop"), Tracker(tmp_path / "cloud")
    add(laptop, 1)  # J0001 on the laptop (a job you added)
    add(cloud, 9)   # J0001 in the cloud (a job it found)
    packet(cloud, "J0001")
    cloud.log("tailored", "J0001")
    counts = sync.merge_db(laptop, cloud.db_path, cloud.root)
    assert counts["renumbered"] == 1
    moved = laptop.find_url("https://boards.greenhouse.io/acme/jobs/9")
    assert moved["id"] == "J0002" and moved["output_dir"] == "output/J0002_acme"
    assert (laptop.root / "output" / "J0002_acme" / "resume.pdf").exists()
    assert laptop.get("J0001")["role"] == "Engineer 1"
    assert any(e["job_id"] == "J0002" and e["message"] == "tailored" for e in laptop.events())


def test_newer_settings_win_and_dismissed_notes_stay_dismissed(tmp_path):
    laptop, cloud = Tracker(tmp_path / "laptop"), Tracker(tmp_path / "cloud")
    cloud.notify("needs-you", "captcha", "J0001")
    sync.merge_db(laptop, cloud.db_path, cloud.root)
    laptop.clear_notifications(note_id=laptop.notifications()[0]["id"])
    time.sleep(1.1)
    laptop.set_setting("apply_mode", "review")
    sync.merge_db(cloud, laptop.db_path, laptop.root)
    assert cloud.setting("apply_mode") == "review" and cloud.notifications() == []


def test_personal_files_go_from_laptop_to_cloud_only(tmp_path):
    laptop, state, cloud = tmp_path / "laptop", tmp_path / "state", tmp_path / "cloud"
    Tracker(laptop)
    (laptop / "profile").mkdir()
    (laptop / "profile" / "baseline.yaml").write_text("mine", encoding="utf-8")
    sync.sync_folders(laptop, state, personal_from_state=False)
    sync.sync_folders(cloud, state, personal_from_state=True)
    assert (cloud / "profile" / "baseline.yaml").read_text(encoding="utf-8") == "mine"
    (cloud / "profile" / "baseline.yaml").write_text("changed in the cloud", encoding="utf-8")
    sync.sync_folders(cloud, state, personal_from_state=True)
    assert (state / "profile" / "baseline.yaml").read_text(encoding="utf-8") == "mine"


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_round_trip_through_a_git_repo(tmp_path):
    try:
        git("--version")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not installed")
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git("clone", "-q", str(remote), str(seed))
    (seed / "README.md").write_text("state", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=seed)
    git("push", "-q", "origin", "HEAD:main", cwd=seed)

    laptop = Tracker(tmp_path / "laptop")
    add(laptop, 1)
    git("clone", "-q", str(remote), str(laptop.root / ".state"))
    sync.sync_with_remote(laptop.root, laptop.root / ".state", say=lambda *_: None)

    # the cloud run: its own clone, merges in, works, merges out, pushes
    cloud_state, cloud = tmp_path / "cloud_state", Tracker(tmp_path / "cloud")
    git("clone", "-q", str(remote), str(cloud_state))
    sync.sync_folders(cloud.root, cloud_state, personal_from_state=True)
    assert cloud.get("J0001")["role"] == "Engineer 1"
    add(cloud, 2)
    sync.sync_with_remote(cloud.root, cloud_state, personal_from_state=True, say=lambda *_: None)

    sync.sync_with_remote(laptop.root, laptop.root / ".state", say=lambda *_: None)
    assert laptop.find_url("https://boards.greenhouse.io/acme/jobs/2")

    # a commit that was never pushed (e.g. GitHub refused it) is pushed on the next sync
    add(laptop, 3)
    sync.sync_with_remote(laptop.root, laptop.root / ".state", push=False, say=lambda *_: None)
    sync.sync_with_remote(laptop.root, laptop.root / ".state", say=lambda *_: None)
    check = tmp_path / "check"
    git("clone", "-q", str(remote), str(check))
    sync.sync_folders(cloud.root, check, personal_from_state=True)
    assert cloud.find_url("https://boards.greenhouse.io/acme/jobs/3")

    # a refused push is reported, never shown as success
    git("remote", "set-url", "origin", str(tmp_path / "missing.git"), cwd=laptop.root / ".state")
    with pytest.raises(sync.SyncError):
        sync.sync_with_remote(laptop.root, laptop.root / ".state", say=lambda *_: None)
