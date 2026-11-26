"""Fetch the full job description for a link that came with only a title (Telegram posts, some alerts).

Public job data only, read as plain text (no browser, no scripts run):
  * Workday, Greenhouse, Lever and Ashby job pages -> their public job-posting JSON;
  * any other job page, only if robots.txt allows it -> the JobPosting data published in its HTML (most
    JavaScript career sites include it for search engines), else the HTML as text if it has enough text.
Links to sites that need an account (LinkedIn, Naukri...) and links that fail the safety screen
(career/linksafety.py) are never fetched. Returns "" when nothing usable was found.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from career import linksafety, sources

MIN_TEXT = 400
TRIED = "(the agent could not fetch the description)"
BLOCKED = ("linkedin.com", "naukri.com", "indeed.com", "glassdoor.", "foundit.", "monster.", "shine.com",
           "instahyre.com", "wellfound.com", "internshala.com")  # same as autoapply.BLOCKED_HOSTS
WORKDAY = re.compile(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/(job/[^?#]+)")
WORKDAY_SITE = re.compile(r"https://(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([\w-]+)/([\w-]+)/"
                          r"(job/[^?#]+)")
GREENHOUSE = re.compile(r"https://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)/jobs/(\d+)")
LEVER = re.compile(r"https://jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")
ASHBY = re.compile(r"https://jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})")
ORACLE = re.compile(r"https://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)/"
                    r"jobs?/(?:preview/)?(\d+)")
DEAD_PAGE = re.compile(r"\b(page not found|not found|404|ats error|an error occurred|no longer (available|accepting)|"
                       r"position has been filled|job (has )?expired)\b", re.I)
# A real job description talks about the work and what is needed; a page of scripts or menus does not.
JD_SIGNALS = re.compile(r"responsibilit|qualification|requirement|experience|you will|what you|skills|"
                        r"about the (role|job|team)|job description|must have|preferred", re.I)


def _workday(data: dict) -> str:
    info = data.get("jobPostingInfo") or {}
    head = "\n".join(x for x in (info.get("title"), info.get("location"), info.get("timeType")) if x)
    return head + "\n\n" + sources.html_to_text(info.get("jobDescription") or "")


LD_JSON = re.compile(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>")
APPLE_FIELDS = ("postingTitle", "jobSummary", "description", "responsibilities", "minimumQualifications",
                "preferredQualifications")


def _job_postings(node):
    if isinstance(node, list):
        for item in node:
            yield from _job_postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        yield from _job_postings(node.get("@graph") or [])


def _structured(page: str) -> str:
    """The description a career page publishes as data inside its HTML: a schema.org JobPosting (what
    Google for Jobs reads; most JavaScript career sites include it) or Apple's page data. Plain text read
    from the page; no script is run."""
    for block in LD_JSON.findall(page or ""):
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        for job in _job_postings(data):
            place = job.get("jobLocation")
            place = place[0] if isinstance(place, list) and place else place
            address = (place or {}).get("address") if isinstance(place, dict) else None
            city = ", ".join(str(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")
                             if isinstance(address, dict) and isinstance(address.get(k), str))
            body = sources.html_to_text(html.unescape(str(job.get("description") or "")))
            if body:
                return f"{job.get('title') or ''}\n{city}\n\n{body}"
    if '\\"jobSummary\\"' in (page or "") or '"jobSummary"' in (page or ""):  # jobs.apple.com
        parts = []
        for name in APPLE_FIELDS:
            m = re.search(r'\\?"' + name + r'\\?":\\?"((?:[^"\\]|\\.)*?)\\?"(?=,\\?")', page)
            if m:
                value = m.group(1).replace('\\\\', '\\')
                try:
                    value = json.loads(f'"{value}"')
                except ValueError:
                    pass
                parts.append(sources.html_to_text(value))
        return "\n\n".join(p for p in parts if p)
    return ""


def fetch_description(url: str, read=sources.fetch_text, read_json=sources.fetch_json,
                      robots=sources.allowed_by_robots) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    if not host or any(b in host for b in BLOCKED) or linksafety.check(url).level != linksafety.OK:
        return ""
    try:
        if m := WORKDAY.match(url):
            tenant, wd, site, path = m.groups()
            text = _workday(read_json(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/{path}"))
        elif m := WORKDAY_SITE.match(url):
            wd, tenant, site, path = m.groups()
            text = _workday(read_json(f"https://{wd}.myworkdaysite.com/wday/cxs/{tenant}/{site}/{path}"))
        elif m := GREENHOUSE.match(url):
            data = read_json(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
            text = f"{data.get('title', '')}\n{(data.get('location') or {}).get('name', '')}\n\n" \
                   + sources.html_to_text(data.get("content") or "")
        elif m := LEVER.match(url):
            data = read_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            lists = "\n".join(f"{x.get('text', '')}\n{sources.html_to_text(x.get('content', ''))}"
                              for x in data.get("lists") or [])
            text = f"{data.get('text', '')}\n{(data.get('categories') or {}).get('location', '')}\n\n" \
                   f"{data.get('descriptionPlain', '')}\n{lists}\n{data.get('additionalPlain', '')}"
        elif m := ORACLE.match(url):
            host, site, job_id = m.groups()
            data = read_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                             f"?expand=all&onlyData=true&finder=ById;Id=%22{job_id}%22,siteNumber={site}")
            item = (data.get("items") or [{}])[0]
            parts = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr",
                     "CorporateDescriptionStr")
            text = "\n".join([item.get("Title") or "", item.get("PrimaryLocation") or "", ""]
                             + [sources.html_to_text(item.get(k) or "") for k in parts])
        elif m := ASHBY.match(url):
            data = read_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}?includeCompensation=true")
            job = next((j for j in data.get("jobs") or [] if j.get("id") == m.group(2)), {})
            text = f"{job.get('title', '')}\n{job.get('location', '')}\n\n{job.get('descriptionPlain', '')}"
        else:
            if not robots(url, read=read):
                return ""
            raw = read(url)
            text = _structured(raw) or sources.html_to_text(
                re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw))
    except Exception:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if DEAD_PAGE.search(text[:400]):  # an error page or a job that has closed
        return ""
    return text if len(text) >= MIN_TEXT and len(set(m.group(0).lower() for m in JD_SIGNALS.finditer(text))) >= 2 \
        else ""


HIRING = re.compile(r"^\W*(.+?)\s+is\s+hiring(?:\s+for)?\s*[:\-–]?\s*(.+)$", re.I)
VAGUE_TITLE = re.compile(r"^\W*(apply|apply here|apply now|apply link|link|click here|\(see the telegram post\)|"
                         r"we'?re hiring.*|hiring|job alert|looking for your next.*)\W*$", re.I)


CODE_LIKE = re.compile(r"[{};=]|\bwindow\b|\bfunction\b|\berror\b", re.I)  # page code or an error page


def better_names(role: str, company: str, text: str) -> dict[str, str]:
    """Real role/company for a post titled "Acme is hiring X" or just "Apply here" (title from the posting)."""
    if m := HIRING.match(role or ""):
        return {"company": company if company and not company.startswith("Unknown") else m.group(1).strip(),
                "role": re.sub(r"^(an?|the)\s+", "", m.group(2).strip(), flags=re.I)}
    if VAGUE_TITLE.match(role or ""):
        first = next((ln.strip() for ln in text.splitlines()
                      if 3 < len(ln.strip()) <= 90 and not CODE_LIKE.search(ln) and not VAGUE_TITLE.match(ln)), "")
        return {"role": re.split(r"\s+[|]\s+", first)[0]} if first else {}  # "Analyst | Acme Careers" -> "Analyst"
    return {}


def _places(tracker) -> list[str]:
    """The `locations` rule from sources.yaml (where a job may be), lower-case."""
    from career.pipeline import CONFIG, load_config
    from career.profile import personal_file
    try:
        return [str(p).lower() for p in load_config(personal_file(CONFIG, tracker.root)).get("locations") or []]
    except Exception:
        return []


STALE_NOTE = "Set aside: still no description after {days} days (old listings are usually closed)"


def set_aside_stale(tracker, now: datetime | None = None, say=print) -> int:
    """Move jobs that have waited for a description longer than `description_wait_days` to skipped, so
    the waiting list holds only listings recent enough to be worth opening. 0 days = never."""
    try:
        days = int(tracker.setting("description_wait_days") or 0)
    except ValueError:
        days = 0
    if days <= 0:
        return 0
    cutoff = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    stale = [r for r in tracker.rows("needs-description") if r["added"] and r["added"] < cutoff]
    for row in stale:
        tracker.update(row["id"], status="skipped",
                       notes="; ".join(n for n in (STALE_NOTE.format(days=days), row["notes"]) if n))
    if stale:
        say(f"  {len(stale)} job(s) still without a description after {days} days set aside (see Skipped).")
    return len(stale)


def fill_missing(tracker, profile, limit: int = 60, delay: float = sources.REQUEST_DELAY_SECONDS,
                 fetch=fetch_description, sleep=time.sleep, say=print, find_on_board=None) -> dict[str, int]:
    """Fetch descriptions for saved jobs still waiting for one, and score them (pipeline.attach_description)."""
    from career.pipeline import attach_description

    if find_on_board is None:
        from career.findjob import find as find_on_board
    counts = {"found": 0, "not_found": 0, "on_company_board": 0, "set_aside": 0}
    counts["set_aside"] = set_aside_stale(tracker, say=say)
    todo = [r for r in tracker.rows("needs-description") if TRIED not in (r["notes"] or "")][:limit]
    places = _places(tracker)
    feeds: dict = {}
    for row in todo:
        url = row["apply_url"] or row["url"]
        if any(b in (urlparse(url).hostname or "") for b in BLOCKED):
            # LinkedIn / Naukri are never opened: look for the same job on the company's own public board.
            names = better_names(row["role"], row["company"], "")
            if names:
                row = tracker.update(row["id"], **names)
            if match := find_on_board(row["company"], row["role"], row["location"], places, cache=feeds):
                if (same := tracker.find_url(match.url)) and same["id"] != row["id"]:
                    tracker.update(row["id"], status="skipped", notes=f"Same job as {same['id']} (found on the "
                                                                      f"company's own job board).")
                    say(f"  {row['id']}: same job as {same['id']}")
                    continue
                tracker.update(row["id"], role=match.title, location=match.location or row["location"],
                               apply_url=match.apply_url)
                row = attach_description(tracker, profile, row["id"], match.description)
                found_note = f"Found on the company's own job board ({match.board}): {match.url}"
                row = tracker.update(row["id"], notes="; ".join(n for n in (found_note, row["notes"]) if n))
                counts["found"] += 1
                counts["on_company_board"] += 1
                say(f"  {row['id']}: found on {match.board} -> fit {row['fit']}, keyword {row['keyword_score']}%")
            else:
                counts["not_found"] += 1
                tracker.update(row["id"], notes=f"{row['notes']} {TRIED}".strip())
            continue
        text = fetch(url)
        if text:
            names = better_names(row["role"], row["company"], text)
            if names:
                tracker.update(row["id"], **names)
            row = attach_description(tracker, profile, row["id"], text)
            counts["found"] += 1
            say(f"  {row['id']}: description found -> fit {row['fit']}, keyword {row['keyword_score']}%")
        else:
            counts["not_found"] += 1
            tracker.update(row["id"], notes=f"{row['notes']} {TRIED}".strip())  # not retried every run
        if not any(b in (urlparse(url).hostname or "") for b in BLOCKED):  # nothing was fetched for those
            sleep(delay)
    return counts
