"""Posts forwarded to your own Telegram bot: only your chat counts, links are safety-screened, nothing read twice."""

import time

import pytest

from career import pipeline, telegrambot
from career.profile import Profile
from career.tracker import Tracker

ME, STRANGER = 1111, 2222


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def message(update_id, chat_id, text, entities=(), group="Tech Jobs India"):
    return {"update_id": update_id, "message": {
        "chat": {"id": chat_id, "type": "private", "first_name": "Me"}, "date": int(time.time()), "text": text,
        "entities": list(entities), "forward_origin": {"type": "channel", "chat": {"title": group}}}}


class FakeBot:
    def __init__(self, updates):
        self.updates, self.sent, self.offsets = updates, [], []

    def __call__(self, method, params=None):
        params = params or {}
        if method == "getMe":
            return {"username": "my_jobs_bot"}
        if method == "getUpdates":
            self.offsets.append(params.get("offset", 0))
            return [u for u in self.updates if u["update_id"] >= int(params.get("offset", 0))]
        if method == "sendMessage":
            self.sent.append(params)
            return {}
        raise AssertionError(method)


def test_setup_remembers_the_chat_that_sent_start(tmp_path):
    t = Tracker(tmp_path)
    bot = FakeBot([message(1, ME, "/start")])
    assert "@my_jobs_bot" in telegrambot.setup(t, call=bot)
    assert t.setting(telegrambot.CHAT_SETTING) == str(ME) and bot.sent[0]["chat_id"] == ME
    with pytest.raises(telegrambot.BotError):
        telegrambot.setup(Tracker(tmp_path / "other"), call=FakeBot([]))


def test_forwards_from_you_become_jobs_and_strangers_are_ignored(tmp_path):
    t = Tracker(tmp_path)
    t.set_setting(telegrambot.CHAT_SETTING, str(ME))
    bot = FakeBot([
        message(5, ME, "Role: Cloud Security Engineer\nCompany: Acme\nLocation: Bengaluru\nApply here",
                entities=[{"type": "text_link", "offset": 70, "length": 10, "url": "https://jobs.acme.com/1"}]),
        message(6, STRANGER, "Role: Fake\nhttps://jobs.evil.example/2"),
    ])
    jobs = telegrambot.collect(t, call=bot, say=lambda *_: None)
    assert [(j.title, j.company, j.url) for j in jobs] == [("Cloud Security Engineer", "Acme",
                                                             "https://jobs.acme.com/1")]
    assert jobs[0].extra["subject"] == "Telegram: Tech Jobs India"
    assert t.setting(telegrambot.OFFSET_SETTING) == "7" and "saved 1 job" in bot.sent[0]["text"]
    assert telegrambot.collect(t, call=bot, say=lambda *_: None) == [] and bot.offsets[-1] == 7  # not read twice


def test_forwarded_links_still_go_through_the_safety_screen(tmp_path, profile):
    t = Tracker(tmp_path)
    t.set_setting(telegrambot.CHAT_SETTING, str(ME))
    bot = FakeBot([message(1, ME, "Role: Analyst\nApply: https://forms.gle/abc")])
    jobs = telegrambot.collect(t, call=bot, say=lambda *_: None)
    counts = pipeline.ingest(t, profile, jobs, resolve=lambda url: url)
    assert counts["unsafe"] == 1 and t.rows()[0]["status"] == "skipped"


def test_nothing_happens_until_the_bot_is_connected(tmp_path):
    def never(*a, **k):
        raise AssertionError("must not call Telegram")
    assert telegrambot.collect(Tracker(tmp_path), call=never) == []
