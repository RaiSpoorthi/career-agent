"""Discovery pipeline: collect listings, drop duplicates, score them offline, save to the tracker.

Nothing here calls Gemini. The agent loop (career/agent.py) decides which listings get a tailored packet.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

import yaml

from career import fit as fitmod
from career import jdfetch, keywords, linksafety, sources
from career.profile import Profile, personal_file
from career.tracker import Tracker

MIN_DESCRIPTION = 200
CONFIG = "config/sources.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or personal_file(CONFIG)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def dedupe_key(job: sources.Job) -> str:
    url = (job.url or "").split("?")[0].split("#")[0].rstrip("/").lower()
    if url:
        return url
    raw = f"{job.company}|{job.title}|{job.location}".lower()
    return "h:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _title_ok(title: str, words: list[str], exclude: list[str] | None = None) -> bool:
    low = (title or "").lower()
    if any(re.search(r"(?<![\w])" + re.escape(x.lower()) + r"(?![\w])", low) for x in exclude or []):
        return False
    return not words or any(w.lower() in low for w in words)


def _location_ok(location: str, places: list[str]) -> bool:
    """Keep a listing when its location is unknown or mentions one of your places."""
    low = (location or "").lower()
    return not places or not low or any(p.lower() in low for p in places)


def discover(cfg: dict[str, Any], fetch_json=sources.fetch_json, fetch_text=sources.fetch_text,
             delay: float = sources.REQUEST_DELAY_SECONDS) -> tuple[list[sources.Job], list[str]]:
    jobs: list[sources.Job] = []
    notes: list[str] = []
    words = cfg.get("title_keywords") or []
    exclude = cfg.get("exclude_title_keywords") or []
    places = cfg.get("locations") or []

    def keep(found: list[sources.Job]) -> list[sources.Job]:
        return [j for j in found if _title_ok(j.title, words, exclude) and _location_ok(j.location, places)]

    def board(kind: str, key: str, url_for, parse) -> None:
        for item in cfg.get(kind) or []:
            try:
                jobs.extend(keep(parse(fetch_json(url_for(item[key])), item)))
            except Exception as exc:
                notes.append(f"{kind.title()} {item.get(key)}: {type(exc).__name__} {str(exc)[:100]}")

    board("greenhouse", "board", sources.greenhouse_url,
          lambda data, item: sources.parse_greenhouse(data, item["company"], item["board"]))
    board("lever", "site", sources.lever_url, lambda data, item: sources.parse_lever(data, item["company"]))
    board("ashby", "board", sources.ashby_url, lambda data, item: sources.parse_ashby(data, item["company"]))
    board("recruitee", "site", sources.recruitee_url,
          lambda data, item: sources.parse_recruitee(data, item["company"]))

    for item in cfg.get("smartrecruiters") or []:
        try:
            listed = keep(sources.parse_smartrecruiters_list(fetch_json(sources.smartrecruiters_url(item["id"])),
                                                             item["company"], item["id"]))
            for index, job in enumerate(listed[: sources.MAX_PAGE_LINKS]):
                if index and delay:
                    time.sleep(delay)
                if job.extra.get("detail_url"):
                    job.description = sources.smartrecruiters_detail(fetch_json(job.extra["detail_url"]))
                jobs.append(job)
        except Exception as exc:
            notes.append(f"SmartRecruiters {item.get('id')}: {type(exc).__name__} {str(exc)[:100]}")

    if cfg.get("remotive"):
        try:
            jobs.extend(keep(sources.parse_remotive(fetch_json(sources.REMOTIVE_URL))))
        except Exception as exc:
            notes.append(f"Remotive: {type(exc).__name__} {str(exc)[:100]}")

    for item in cfg.get("careers_pages") or []:
        found, warnings = sources.careers_page(item["company"], item["url"], item.get("keywords") or words,
                                               read=fetch_text, delay=delay)
        jobs += [j for j in found if _title_ok(j.title, [], exclude)]
        notes += warnings
    return jobs, notes


SOURCE_KINDS = ("greenhouse", "lever", "ashby", "recruitee", "smartrecruiters", "remotive", "careers_pages")


def _unvetted(job: sources.Job) -> bool:
    """Links posted by strangers (Telegram groups) are screened before anything opens them."""
    return job.source == "telegram" or str(job.extra.get("subject", "")).startswith("Telegram")


def ingest(tracker: Tracker, profile: Profile, jobs: list[sources.Job],
           resolve=linksafety.resolve) -> dict[str, int]:
    counts = {"new": 0, "duplicate": 0, "needs_description": 0, "auto_skipped": 0, "unsafe": 0}
    for job in jobs:
        key = dedupe_key(job)
        if tracker.find_key(key) or (job.url and tracker.find_url(job.url)):
            counts["duplicate"] += 1
            continue
        if _unvetted(job):
            verdict = linksafety.screen(job.url, job.description, resolve=resolve)
            if verdict.level != linksafety.OK:
                tracker.add(company=job.company or "Unknown (Telegram)", role=job.title, url=job.url,
                            location=job.location, text=job.description or job.title,
                            track=next(iter(profile.tracks)), keyword_score=0, fit=fitmod.SKIP, status="skipped",
                            source=job.source, dedupe_key=key,
                            notes=f"Unsafe link, never opened by the agent: {verdict.reason}")
                counts["unsafe"] += 1
                continue
            if verdict.url != job.url:  # a short link: keep where it really leads
                canon = sources._canonical(verdict.url)
                job.url, job.source = canon if canon else (verdict.url, job.source)
                job.extra["apply_url"] = "" if canon else verdict.url
                key = dedupe_key(job)
                if tracker.find_key(key) or tracker.find_url(job.url):
                    counts["duplicate"] += 1
                    continue
        text = (job.description or "").strip()
        if len(text) < MIN_DESCRIPTION:
            stub = f"{job.title}\n{job.company}\n{job.location}\n{job.url}\n{job.extra.get('subject', '')}".strip()
            tracker.add(company=job.company or "Unknown (from alert)", role=job.title, url=job.url,
                        location=job.location, text=stub, track=next(iter(profile.tracks)), keyword_score=0, fit="",
                        status="needs-description", source=job.source, dedupe_key=key,
                        notes="Full description not available: open the link and paste it in (UI or jobs.py attach)")
            counts["needs_description"] += 1
            continue
        kw = keywords.match(profile, text)
        report = fitmod.check(profile, text, job.location, job.title)
        status = "skipped" if report.verdict == fitmod.SKIP else "discovered"
        tracker.add(company=job.company, role=job.title, url=job.url, location=job.location or report.location,
                    text=text, track=kw.track, keyword_score=kw.score, fit=report.verdict, status=status,
                    source=job.source, dedupe_key=key, notes="; ".join(report.reasons),
                    apply_url=job.extra.get("apply_url", ""))
        counts["auto_skipped" if status == "skipped" else "new"] += 1
    return counts


RESCORED = ("discovered", "packet-ready", "awaiting-human")


def rescore(tracker: Tracker, profile: Profile) -> dict[str, int]:
    """Re-apply your current rules to saved jobs, e.g. after you change preferences in baseline.yaml.

    Updates fit, keyword score, notes and pay. A job not yet worked on ("discovered") that now breaks a
    rule is marked skipped; jobs already tailored or waiting for you keep their status (the new
    warning shows on them). Jobs you added yourself are never auto-skipped. Pay is filled in for every job.
    """
    counts = {"rescored": 0, "now_skipped": 0}
    for row in tracker.rows():
        path = tracker.jd_dir / f"{row['id']}.txt"
        if not path.exists() or row["status"] == "needs-description":
            continue
        text = path.read_text(encoding="utf-8")
        changes = {"salary": fitmod.stated_pay(text)}
        if row["status"] in RESCORED:
            kw = keywords.match(profile, text)
            report = fitmod.check(profile, text, row["location"], row["role"])
            changes.update(keyword_score=str(kw.score), fit=report.verdict, notes="; ".join(report.reasons))
            if row["status"] == "discovered":
                changes["track"] = kw.track
                if report.verdict == fitmod.SKIP and row["source"] != "manual":
                    changes["status"] = "skipped"
                    counts["now_skipped"] += 1
            counts["rescored"] += 1
        if any(row[k] != v for k, v in changes.items()):
            tracker.update(row["id"], **changes)
    return counts


def add_manual(tracker: Tracker, profile: Profile, *, url: str, text: str, company: str, role: str,
               location: str = "") -> tuple[dict[str, str], bool]:
    """Save a job you found yourself and score it. Returns (row, is_new); a link saved before is reused.

    Your own pick is never auto-skipped: fit warnings are kept in its notes for you to read.
    A saved job that is still waiting for its description (e.g. a LinkedIn link from Telegram) gets this text.
    """
    text, url = (text or "").strip(), (url or "").strip()
    if len(text) < 80:
        raise ValueError("That job description looks too short. Paste the full text.")
    if url and (dup := tracker.find_listing(url)):
        if dup["status"] == "needs-description":
            # Names from the page replace placeholders such as "Unknown (from alert)" or "Apply Now".
            def placeholder(old: str) -> bool:
                old = (old or "").strip()
                return not old or old.lower().startswith("unknown") or bool(jdfetch.VAGUE_TITLE.match(old))

            names = {k: v.strip() for k, v in (("company", company), ("role", role), ("location", location))
                     if v.strip() and placeholder(dup[k])}
            if names:
                tracker.update(dup["id"], **names)
            dup = attach_description(tracker, profile, dup["id"], text)
        return dup, False
    kw = keywords.match(profile, text)
    report = fitmod.check(profile, text, location, role)
    row = tracker.add(company=company.strip() or "Unknown", role=role.strip() or "Unknown", url=url,
                      location=location.strip() or report.location, text=text, track=kw.track,
                      keyword_score=kw.score, fit=report.verdict, notes="; ".join(report.reasons), source="manual")
    return row, True


def attach_description(tracker: Tracker, profile: Profile, job_id: str, text: str) -> dict[str, str]:
    """Add the full job description to a listing (e.g. one that came from an alert email) and score it."""
    text = (text or "").strip()
    if len(text) < 80:
        raise ValueError("That description is too short. Paste the full job description.")
    row = tracker.get(job_id)
    tracker.write_jd(row["id"], text)
    kw = keywords.match(profile, text)
    report = fitmod.check(profile, text, row["location"], row["role"])
    changes = {"track": kw.track, "keyword_score": str(kw.score), "fit": report.verdict,
               "notes": "; ".join(report.reasons), "salary": fitmod.stated_pay(text)}
    if not row["location"]:
        changes["location"] = report.location
    if row["status"] == "needs-description":
        changes["status"] = "skipped" if report.verdict == fitmod.SKIP else "discovered"
    return tracker.update(row["id"], **changes)
