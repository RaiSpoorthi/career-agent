"""Job sources: public job boards, public career pages, and job-alert emails.

Rules this module follows:
  * Only public endpoints. No logins, no cookies, no account-based access.
  * LinkedIn and Naukri pages are never fetched. Only the links inside alert emails you received are read.
  * Career pages are fetched only if robots.txt allows it, with a delay between requests.
"""

from __future__ import annotations

import email
import html
import json
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from email import policy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

USER_AGENT = "CareerAgent/1.0 (personal job search; respects robots.txt)"
REQUEST_DELAY_SECONDS = 2.0
MAX_PAGE_LINKS = 25


@dataclass
class Job:
    source: str  # greenhouse, lever, careers-page, linkedin-alert, naukri-alert, email-alert
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HTTP (standard library only, so nothing extra to install)
# ---------------------------------------------------------------------------
def _request(url: str, accept: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - https URLs from your own config
        return resp.read().decode("utf-8", "replace")


def fetch_json(url: str):
    return json.loads(_request(url, "application/json"))


def fetch_text(url: str) -> str:
    return _request(url, "text/html,application/xhtml+xml,*/*")


def html_to_text(raw: str) -> str:
    """Turn HTML (or HTML-escaped HTML) into plain text with line breaks kept."""
    text = html.unescape(raw or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Public job boards
# ---------------------------------------------------------------------------
def greenhouse_url(board: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def lever_url(site: str) -> str:
    return f"https://api.lever.co/v0/postings/{site}?mode=json"


def parse_greenhouse(data: dict, company: str, board: str = "") -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        # absolute_url is often the company's own site with the form in a frame. The hosted
        # board page is the plain form, so that is what the apply step opens.
        hosted = f"https://job-boards.greenhouse.io/{board}/jobs/{item['id']}" if board and item.get("id") else ""
        jobs.append(Job(
            source="greenhouse", title=item.get("title", ""), company=company,
            url=item.get("absolute_url", ""),
            location=(item.get("location") or {}).get("name", ""),
            description=html_to_text(item.get("content", "")),
            extra={"apply_url": hosted},
        ))
    return jobs


def parse_lever(data: list, company: str) -> list[Job]:
    jobs = []
    for item in data if isinstance(data, list) else []:
        cats = item.get("categories") or {}
        body = item.get("descriptionPlain") or html_to_text(item.get("description", ""))
        extras = "\n".join(html_to_text(block.get("content", "")) for block in item.get("lists", []))
        jobs.append(Job(
            source="lever", title=item.get("text", ""), company=company,
            url=item.get("hostedUrl", ""), location=cats.get("location", ""),
            description=(body + "\n" + extras).strip(),
            extra={"apply_url": item.get("applyUrl") or (item.get("hostedUrl", "").rstrip("/") + "/apply"
                                                         if item.get("hostedUrl") else "")},
        ))
    return jobs


# --- More free public job-board APIs (no key, no login) -------------------------------------
def ashby_url(board: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"


def parse_ashby(data: dict, company: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        location = item.get("location", "") or ""
        if item.get("isRemote") and "remote" not in location.lower():
            location = (location + " (Remote)").strip()
        pay = ((item.get("compensation") or {}).get("compensationTierSummary")) or ""
        body = item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml", ""))
        jobs.append(Job(source="ashby", title=item.get("title", ""), company=company, url=item.get("jobUrl", ""),
                        location=location, description=(body + ("\nCompensation: " + pay if pay else "")).strip(),
                        extra={"apply_url": item.get("applyUrl", "")}))
    return jobs


def recruitee_url(site: str) -> str:
    return f"https://{site}.recruitee.com/api/offers/"


def parse_recruitee(data: dict, company: str) -> list[Job]:
    jobs = []
    for item in data.get("offers", []):
        body = html_to_text(item.get("description", "")) + "\n" + html_to_text(item.get("requirements", ""))
        location = item.get("location") or ", ".join(x for x in (item.get("city"), item.get("country")) if x)
        if item.get("remote") and "remote" not in location.lower():
            location = (location + " (Remote)").strip()
        jobs.append(Job(source="recruitee", title=item.get("title", ""), company=company,
                        url=item.get("careers_url", ""), location=location, description=body.strip(),
                        extra={"apply_url": item.get("careers_apply_url", "")}))
    return jobs


def smartrecruiters_url(company_id: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings?limit=100"


def parse_smartrecruiters_list(data: dict, company: str, company_id: str) -> list[Job]:
    """The list has no description; `smartrecruiters_detail` fills it in for the listings you keep."""
    jobs = []
    for item in data.get("content", []):
        loc = item.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("country", "").upper()) if x)
        if loc.get("remote"):
            location = (location + " (Remote)").strip()
        jobs.append(Job(source="smartrecruiters", title=item.get("name", ""), company=company,
                        url=f"https://jobs.smartrecruiters.com/{company_id}/{item.get('id', '')}",
                        location=location, extra={"detail_url": item.get("ref", "")}))
    return jobs


def smartrecruiters_detail(data: dict) -> str:
    sections = ((data.get("jobAd") or {}).get("sections")) or {}
    parts = [html_to_text((sections.get(k) or {}).get("text", ""))
             for k in ("jobDescription", "qualifications", "additionalInformation")]
    return "\n".join(p for p in parts if p)


REMOTIVE_URL = "https://remotive.com/api/remote-jobs?category=software-dev"


def parse_remotive(data: dict) -> list[Job]:
    """Remotive lists remote jobs. Their terms ask for a link back and only a few requests a day."""
    jobs = []
    for item in data.get("jobs", []):
        where = item.get("candidate_required_location", "") or "Worldwide"
        pay = item.get("salary") or ""
        jobs.append(Job(source="remotive", title=item.get("title", ""), company=item.get("company_name", ""),
                        url=item.get("url", ""), location=f"Remote ({where})",
                        description=(html_to_text(item.get("description", "")) +
                                     ("\nSalary: " + pay if pay else "")).strip()))
    return jobs


# ---------------------------------------------------------------------------
# Public career pages (robots.txt aware)
# ---------------------------------------------------------------------------
class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None


def allowed_by_robots(url: str, read=fetch_text) -> bool:
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        body = read(robots_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # no robots.txt means nothing is disallowed
            return True
        return False  # be conservative when the file can't be read
    except Exception:
        return False
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def careers_page(company: str, url: str, keywords: list[str], read=fetch_text, delay: float = REQUEST_DELAY_SECONDS,
                 max_links: int = MAX_PAGE_LINKS) -> tuple[list[Job], list[str]]:
    """Follow links on a careers page whose text matches a keyword. Returns (jobs, warnings)."""
    warnings: list[str] = []
    if not allowed_by_robots(url, read):
        return [], [f"{company}: robots.txt does not allow crawling {url}; skipped"]
    try:
        page = read(url)
    except Exception as exc:
        return [], [f"{company}: could not read {url} ({type(exc).__name__})"]

    parser = _Links()
    parser.feed(page)
    host = urlparse(url).netloc
    wanted, seen = [], set()
    for href, text in parser.links:
        link = urljoin(url, href)
        if urlparse(link).netloc != host or link in seen:
            continue
        if any(k.lower() in text.lower() for k in keywords) and len(text) > 3:
            seen.add(link)
            wanted.append((link, text))
    jobs: list[Job] = []
    for index, (link, title) in enumerate(wanted[:max_links]):
        if index and delay:
            time.sleep(delay)
        if not allowed_by_robots(link, read):
            continue
        try:
            detail = html_to_text(read(link))[:20000]
        except Exception:
            detail = ""
        jobs.append(Job(source="careers-page", title=title, company=company, url=link, description=detail))
    if len(wanted) > max_links:
        warnings.append(f"{company}: {len(wanted) - max_links} more links not checked (limit {max_links})")
    return jobs, warnings


# ---------------------------------------------------------------------------
# Job-alert emails (LinkedIn, Naukri, others). Reads only messages you received.
# ---------------------------------------------------------------------------
LINKEDIN_JOB = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)
NAUKRI_JOB = re.compile(r"https?://(?:www\.)?naukri\.com/job-listings[^\s\"'<>]*", re.I)
GENERIC_TEXT = {"", "view", "view job", "apply", "apply now", "see job", "job alert", "view details"}


def _canonical(url: str) -> tuple[str, str] | None:
    m = LINKEDIN_JOB.search(url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}/", "linkedin-alert"
    m = NAUKRI_JOB.search(url)
    if m:
        return m.group(0).split("?")[0], "naukri-alert"
    return None


def parse_alert_eml(raw: bytes) -> list[Job]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    sender_subject = f"{msg.get('from', '')} {msg.get('subject', '')}".lower()
    body_part = msg.get_body(preferencelist=("html", "plain"))
    content = body_part.get_content() if body_part else ""
    collector = _Links()
    collector.feed(content)

    jobs: dict[str, Job] = {}
    for href, text in collector.links:
        canon = _canonical(html.unescape(href))
        if not canon:
            continue
        url, source = canon
        title = text if text.lower() not in GENERIC_TEXT else ""
        if url not in jobs:
            jobs[url] = Job(source=source, title=title, company="", url=url,
                            extra={"subject": msg.get("subject", "")})
        elif title and not jobs[url].title:
            jobs[url].title = title
    for match in list(LINKEDIN_JOB.finditer(content)) + list(NAUKRI_JOB.finditer(content)):
        canon = _canonical(match.group(0))
        if canon and canon[0] not in jobs:
            jobs[canon[0]] = Job(source=canon[1], title="", company="", url=canon[0],
                                 extra={"subject": msg.get("subject", "")})
    if not jobs and "alert" in sender_subject:
        return []
    for job in jobs.values():
        if not job.title:
            job.title = "(title in the alert email; open the link)"
    return list(jobs.values())


def read_inbox(folder: Path) -> tuple[list[Job], list[str]]:
    """Parse every .eml in the folder, then move each file to folder/processed."""
    folder.mkdir(parents=True, exist_ok=True)
    processed = folder / "processed"
    processed.mkdir(exist_ok=True)
    jobs: list[Job] = []
    notes: list[str] = []
    for path in sorted(folder.glob("*.eml")):
        found = parse_alert_eml(path.read_bytes())
        if not found:
            notes.append(f"{path.name}: no LinkedIn or Naukri job links found")
        jobs.extend(found)
        path.replace(processed / path.name)
    return jobs, notes
