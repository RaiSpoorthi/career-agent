"""Find a LinkedIn / Naukri job on the company's own public job board, so the agent can read it and apply there.

LinkedIn and Naukri are never opened. A job that reached you through an alert email or a Telegram post comes
with its company and title. This looks for that company's public job feed (Greenhouse, Lever, Ashby,
SmartRecruiters: the same free JSON feeds the agent already reads) and for a posting with the same title, in a
place your rules allow. Only a close title match counts; otherwise the job stays under "needs a description".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote

from career import sources

MIN_SIMILARITY = 0.85
SUFFIXES = re.compile(r"\b(private|pvt|ltd|limited|inc|llc|llp|corp(oration)?|co|plc|gmbh|technologies|technology|"
                      r"tech|software|solutions|services|systems|labs?|india|global|group|the)\b\.?", re.I)
UNKNOWN = re.compile(r"^\s*(unknown|\(|$)", re.I)


@dataclass
class Match:
    url: str          # the public posting (its form is where the agent applies)
    apply_url: str
    title: str
    location: str
    description: str
    board: str        # e.g. "greenhouse:phonepe"


def slugs(company: str) -> list[str]:
    """Likely job-board names for a company: 'PhonePe Pvt. Ltd.' -> ['phonepe', 'phone-pe', ...]."""
    base = SUFFIXES.sub(" ", company.lower().replace("&", " and "))
    words = re.findall(r"[a-z0-9]+", base.replace(".", ""))
    if not words:
        return []
    found = ["".join(words), "-".join(words), words[0]]
    dotted = re.sub(r"[^a-z0-9.]", "", company.lower())  # Media.net -> medianet (dot dropped above), media.net
    found += [dotted.replace(".", ""), dotted.replace(".", "-")]
    return [s for s in dict.fromkeys(found) if len(s) >= 3]


def _norm_title(title: str) -> str:
    title = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", title.lower())
    title = re.sub(r"\b(sde)\b", "software development engineer", title)
    title = re.sub(r"\b(engg|enigneer|eningeer|enginer|engineeer)\b", "engineer", title)  # common typos in posts
    title = re.sub(r"\bsr\b\.?", "senior", title)
    return " ".join(re.findall(r"[a-z0-9]+", title))


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio()


def _feeds(slug: str, fetch_json) -> list[tuple[str, list[sources.Job]]]:
    out = []
    for kind, url, parse in (
            ("greenhouse", sources.greenhouse_url(slug), lambda d: sources.parse_greenhouse(d, slug, slug)),
            ("lever", sources.lever_url(slug), lambda d: sources.parse_lever(d, slug)),
            ("ashby", sources.ashby_url(slug), lambda d: sources.parse_ashby(d, slug))):
        try:
            jobs = parse(fetch_json(url))
        except Exception:
            continue
        if jobs:
            out.append((f"{kind}:{slug}", jobs))
    return out


def _smartrecruiters(slug: str, title: str, fetch_json) -> list[tuple[str, list[sources.Job]]]:
    try:
        data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=50&q={quote(title)}")
        listed = sources.parse_smartrecruiters_list(data, slug, slug)
    except Exception:
        return []
    close = [j for j in listed if similarity(title, j.title) >= MIN_SIMILARITY and j.extra.get("detail_url")][:5]
    for job in close:  # the list has no descriptions: read only the postings that could be this job
        try:
            job.description = sources.smartrecruiters_detail(fetch_json(job.extra["detail_url"]))
        except Exception:
            job.description = ""
    return [(f"smartrecruiters:{slug}", close)] if close else []


def find(company: str, title: str, location: str = "", places: list[str] | None = None,
         fetch_json=sources.fetch_json, cache: dict | None = None) -> Match | None:
    """The same job on the company's own public board, or None. `cache` keeps feeds for one run."""
    if not company or UNKNOWN.match(company) or not title or title.startswith("("):
        return None
    cache = {} if cache is None else cache
    want_place = (location or "").lower().split(",")[0].strip()
    best: tuple[float, int, Match] | None = None
    for slug in slugs(company):
        if slug not in cache:
            cache[slug] = _feeds(slug, fetch_json)
        boards = cache[slug] or _smartrecruiters(slug, title, fetch_json)
        for board, jobs in boards:
            for job in jobs:
                score = similarity(title, job.title)
                if score < MIN_SIMILARITY or not job.description:
                    continue
                where = (job.location or "").lower()
                if places and where and not any(p in where for p in places):
                    continue  # e.g. the same title in another country
                near = int(bool(want_place) and want_place in where) + int("india" in where or "bengaluru" in where
                                                                          or "bangalore" in where)
                match = Match(url=job.url, apply_url=job.extra.get("apply_url") or job.url, title=job.title,
                              location=job.location, description=job.description, board=board)
                if best is None or (score, near) > best[:2]:
                    best = (score, near, match)
        if best:
            break  # this slug is the company's board
    return best[2] if best else None
