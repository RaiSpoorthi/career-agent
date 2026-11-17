"""Optional: read your job-alert emails straight from Gmail (read-only).

Setup is in README under "Gmail alerts". In short:
  1. Gmail: create a filter that labels LinkedIn and Naukri alert emails "job-alerts".
  2. Google Cloud (free): create a project, enable the Gmail API, create an OAuth "Desktop app"
     client, download it as client_secret.json into this folder.
  3. First run asks you to sign in and allow read-only access. A token is saved locally.

Scope is gmail.readonly: the program can read messages, never send, delete or modify them.
This module is not tested against live Google servers; the inbox/ folder method always works.
"""

from __future__ import annotations

import base64
from pathlib import Path

from career.profile import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_QUERY = "label:job-alerts newer_than:2d"


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


def fetch_alert_messages(root: Path = ROOT, query: str = DEFAULT_QUERY, limit: int = 50) -> list[bytes]:
    """Return raw RFC 822 bytes for each matching message."""
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
