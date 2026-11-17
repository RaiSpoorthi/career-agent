"""Optional: read your job-alert emails straight from Gmail (read-only).

Two ways (README, "Job alerts from your email"):
  * Easiest: an app password. In .env set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (Google Account ->
    Security -> 2-Step Verification -> App passwords). The mailbox is opened read-only over IMAP and alert
    emails from LinkedIn, Naukri and other job sites are found by sender; nothing is marked read or changed.
  * Google API: an OAuth "Desktop app" client saved as client_secret.json (gmail.readonly scope).

Only alert emails you received are read, and only their job links are used.
"""

from __future__ import annotations

import base64
import imaplib
import os
from pathlib import Path

from career.profile import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_QUERY = "label:job-alerts newer_than:2d"
# Senders of job-alert emails. Gmail's own search syntax (X-GM-RAW) finds them in every folder.
ALERT_SENDERS = ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com",
                 "naukri.com", "naukrialerts", "foundit.in", "instahyre.com", "wellfound.com", "cutshort.io",
                 "hirist", "iimjobs.com", "glassdoor"]
IMAP_DAYS = 3


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
                   connect=imaplib.IMAP4_SSL) -> list[bytes]:
    """Raw alert emails from the last `days` days, read-only (EXAMINE + BODY.PEEK: nothing marked read)."""
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
        query = f"from:({' OR '.join(ALERT_SENDERS)}) newer_than:{days}d"
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
