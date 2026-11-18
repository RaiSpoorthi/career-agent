"""Optional: read your job-alert emails straight from Gmail (read-only).

Two ways (README, "Job alerts from your email"):
  * Easiest: an app password. In .env set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (Google Account ->
    Security -> 2-Step Verification -> App passwords). The mailbox is opened read-only over IMAP and alert
    emails from LinkedIn, Naukri and other job sites are found by sender; nothing is marked read or changed.
  * Google API: an OAuth "Desktop app" client saved as client_secret.json (gmail.readonly scope).

Only alert emails you received are read, and only their job links are used. When an application form
emails a security code (Greenhouse does), the agent reads only mail from application systems
(CODE_SENDERS) from the last hour to copy that code; nothing else in your mailbox is read.
"""

from __future__ import annotations

import base64
import email
import imaplib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from career.profile import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_QUERY = "label:job-alerts newer_than:2d"
# Senders of job-alert emails. Gmail's own search syntax (X-GM-RAW) finds them in every folder.
ALERT_SENDERS = ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com",
                 "naukri.com", "naukrialerts", "foundit.in", "instahyre.com", "wellfound.com", "cutshort.io",
                 "hirist", "iimjobs.com", "glassdoor"]
IMAP_DAYS = 3
# Application systems that email a security code to confirm a submission. Only their mail is read for codes.
CODE_SENDERS = ["greenhouse-mail.io", "greenhouse.io", "lever.co", "hire.lever.co", "ashbyhq.com", "recruitee.com"]
CODE_AFTER_LABEL = re.compile(r"code[^\n:]{0,80}:\s*([A-Za-z0-9]{6,10})\b", re.I)
CODE_LINE = re.compile(r"^\s*([A-Za-z0-9]{6,10})\s*$", re.M)


class GmailError(RuntimeError):
    pass


def _credentials(client_secret: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES) if token_path.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not client_secret.exists():
            raise GmailError(f"Missing {client_secret.name}. See README, section 'Gmail alerts'.")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def imap_configured() -> bool:
    from career.llm import load_env

    load_env()
    return bool(os.getenv("GMAIL_ADDRESS", "").strip() and os.getenv("GMAIL_APP_PASSWORD", "").strip())


def fetch_via_imap(address: str, app_password: str, days: int = IMAP_DAYS, limit: int = 100,
                   connect=imaplib.IMAP4_SSL, query: str = "") -> list[bytes]:
    """Raw alert emails (or those matching `query`), read-only (EXAMINE + BODY.PEEK: nothing marked read)."""
    try:
        conn = connect("imap.gmail.com")
        conn.login(address, app_password.replace(" ", ""))
    except imaplib.IMAP4.error as exc:
        raise GmailError("Gmail refused the app password. Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env "
                         "(an app password is 16 letters; your normal password won't work).") from exc
    except OSError as exc:
        raise GmailError(f"Could not reach Gmail ({type(exc).__name__}). Check your internet connection.") from exc
    try:
        status, _ = conn.select('"[Gmail]/All Mail"', readonly=True)
        if status != "OK":  # the folder has another name in some languages
            conn.select("INBOX", readonly=True)
        query = query or f"from:({' OR '.join(ALERT_SENDERS)}) newer_than:{days}d"
        status, data = conn.search(None, "X-GM-RAW", f'"{query}"')
        ids = (data[0] or b"").split()[-limit:] if status == "OK" else []
        messages = []
        for msg_id in ids:
            status, parts = conn.fetch(msg_id, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if status == "OK" and raw:
                messages.append(raw)
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_alerts(root: Path = ROOT) -> list[bytes]:
    """Alert emails from Gmail by whichever method is set up (app password first). [] if neither is."""
    if imap_configured():
        return fetch_via_imap(os.environ["GMAIL_ADDRESS"].strip(), os.environ["GMAIL_APP_PASSWORD"])
    if (root / "gmail_token.json").exists() or (root / "client_secret.json").exists():
        return fetch_alert_messages(root)
    return []


def fetch_alert_messages(root: Path = ROOT, query: str = DEFAULT_QUERY, limit: int = 50) -> list[bytes]:
    """Google API method: raw RFC 822 bytes for each matching message."""
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GmailError("Install the Gmail packages: "
                         "pip install google-api-python-client google-auth-oauthlib") from exc
    creds = _credentials(root / "client_secret.json", root / "gmail_token.json")
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    listed = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    messages = []
    for item in listed.get("messages", []):
        raw = service.users().messages().get(userId="me", id=item["id"], format="raw").execute()["raw"]
        messages.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return messages


# ---- security codes -------------------------------------------------------------------------------
def mail_configured(root: Path = ROOT) -> bool:
    return imap_configured() or (root / "gmail_token.json").exists()


def fetch_code_emails(root: Path = ROOT) -> list[bytes]:
    """Mail from application systems (CODE_SENDERS) received in the last hour, read-only."""
    query = f"from:({' OR '.join(CODE_SENDERS)}) newer_than:1h"
    if imap_configured():
        return fetch_via_imap(os.environ["GMAIL_ADDRESS"].strip(), os.environ["GMAIL_APP_PASSWORD"],
                              query=query, limit=10)
    if (root / "gmail_token.json").exists():
        return fetch_alert_messages(root, query=query, limit=10)
    return []


def _body_text(msg) -> str:
    from career.sources import html_to_text

    plain, html = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            plain += text
        elif part.get_content_type() == "text/html":
            html += text
    return plain or html_to_text(html)


def extract_code(raw: bytes, since: datetime | None = None) -> str | None:
    """The security code in one email from an application system, if it arrived after `since`."""
    msg = email.message_from_bytes(raw)
    sender = (msg.get("From") or "").lower()
    if not any(s in sender for s in CODE_SENDERS):
        return None
    if since:
        try:
            if parsedate_to_datetime(msg.get("Date")) < since - timedelta(minutes=1):
                return None
        except (TypeError, ValueError):
            return None
    text = _body_text(msg)
    if not re.search(r"(security|verification|confirmation) code", text, re.I):
        return None
    found = CODE_AFTER_LABEL.findall(text) or [c for c in CODE_LINE.findall(text) if re.search(r"\d", c)]
    return found[0] if found else None


def wait_for_code(since: datetime, root: Path = ROOT, timeout: int = 180, poll: int = 15,
                  fetch=None, sleep=time.sleep) -> str | None:
    """Poll for a security code emailed after `since` (an aware datetime). None if none arrives in time."""
    fetch = fetch or (lambda: fetch_code_emails(root))
    deadline = time.time() + timeout
    while True:
        codes = [c for c in (extract_code(raw, since) for raw in fetch()) if c]
        if codes:
            return codes[-1]
        if time.time() >= deadline:
            return None
        sleep(poll)


def now() -> datetime:
    return datetime.now(timezone.utc)
