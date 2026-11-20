"""Job posts you forward to your own Telegram bot (one tap in Telegram: Forward -> your bot).

The agent never logs in to Telegram or reads any group. You make a private bot with @BotFather, put its token in
.env as TELEGRAM_BOT_TOKEN, send it /start once, and run `python jobs.py telegram-setup` so it remembers your chat.
After that every run collects what you forwarded since the last run (Telegram keeps it for 24 hours) and replies
with what it saved. Messages from anyone else are ignored. Every link still goes through career/linksafety.py.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from career import sources

API = "https://api.telegram.org/bot{token}/{method}"
CHAT_SETTING = "telegram_chat_id"
OFFSET_SETTING = "telegram_offset"


class BotError(RuntimeError):
    pass


def token() -> str:
    from career.llm import load_env
    load_env()  # .env never overrides a value already set (tests set it empty)
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def call(method: str, params: dict | None = None, bot_token: str = "") -> dict | list:
    bot_token = bot_token or token()
    if not bot_token:
        raise BotError("No TELEGRAM_BOT_TOKEN in .env")
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=bot_token, method=method), data=data,
                                 headers={"User-Agent": sources.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - Telegram's own API
            answer = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # never show the token (it is part of the URL)
        raise BotError(f"Telegram did not answer ({type(exc).__name__})") from None
    if not answer.get("ok"):
        raise BotError(f"Telegram said: {answer.get('description', 'error')}")
    return answer["result"]


def _as_export_message(msg: dict) -> dict:
    """A Bot API message in the shape of a Telegram Desktop export message (what sources already parses)."""
    text = msg.get("text") or msg.get("caption") or ""
    entities = msg.get("entities") or msg.get("caption_entities") or []
    hidden = [{"text": "", "href": e["url"]} for e in entities if e.get("type") == "text_link" and e.get("url")]
    date = datetime.fromtimestamp(int(msg.get("date") or 0), tz=timezone.utc)  # when you forwarded it
    return {"type": "message", "date": date.strftime("%Y-%m-%dT%H:%M:%S"), "text": [text, *hidden]}


def _group(msg: dict) -> str:
    origin = msg.get("forward_origin") or {}
    chat = origin.get("chat") or msg.get("forward_from_chat") or {}
    return chat.get("title") or "forwarded to your bot"


def collect(tracker, call=call, say=print) -> list[sources.Job]:
    """Jobs from posts forwarded to the bot since the last run, only from your own chat."""
    chat_id = tracker.setting(CHAT_SETTING) or ""
    if not chat_id:
        return []
    offset = int(tracker.setting(OFFSET_SETTING) or 0)
    updates = call("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": '["message"]'})
    jobs: list[sources.Job] = []
    last = offset - 1
    for update in updates:
        last = max(last, int(update["update_id"]))
        msg = update.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue  # someone else found the bot: ignored
        chat = {"name": _group(msg), "messages": [_as_export_message(msg)]}
        jobs += sources.parse_telegram_export(chat, max_age_days=30)
    if updates:
        tracker.set_setting(OFFSET_SETTING, str(last + 1))  # Telegram drops what came before this
    if jobs:
        try:
            note = (f"Career Agent saved {len(jobs)} job link(s) from your forwards. "
                    "Each one is safety-checked before it is opened.")
            call("sendMessage", {"chat_id": chat_id, "text": note})
        except BotError as exc:
            say(f"  Telegram bot: {exc}")
    return jobs


def setup(tracker, call=call) -> str:
    """Remember the chat of whoever sent /start to the bot (you, right after creating it)."""
    me = call("getMe")
    starts = [u["message"] for u in call("getUpdates", {"timeout": 0})
              if (u.get("message") or {}).get("text", "").startswith("/start")
              and (u["message"].get("chat") or {}).get("type") == "private"]
    if not starts:
        raise BotError(f"Open Telegram, find @{me.get('username')}, press Start (or send /start), then run this again.")
    chat = starts[-1]["chat"]
    tracker.set_setting(CHAT_SETTING, str(chat["id"]))
    call("sendMessage", {"chat_id": chat["id"], "text": "Connected to Career Agent. Forward job posts here; every run "
                                                        "(7:00 and 18:00) picks them up."})
    return f"@{me.get('username')} is connected to {chat.get('first_name') or 'your'} chat."
