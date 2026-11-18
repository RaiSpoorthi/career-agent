"""Unvetted links are screened before anything opens them; security codes come only from application systems."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from career import gmail, linksafety, pipeline, sources
from career.profile import Profile
from career.tracker import Tracker

JD = ("Software Engineer, Bengaluru. Build REST APIs in Python and React with Git and CI/CD. 0-2 years of "
      "experience. You will write tests, review code and ship features with the product team every week. "
      "You will work closely with designers and customers, debug production issues and improve our tooling.") * 2


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


@pytest.mark.parametrize("url, level", [
    ("https://boards.greenhouse.io/acme/jobs/1", "ok"),
    ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/123", "ok"),
    ("https://careers.leveragepartners.com/job/1", "ok"),  # 'lever' inside a word is not a look-alike
    ("https://bit.ly/3abcd", "redirect"),
    ("https://artha.link/xyz", "redirect"),
    ("https://www.google.com/url?q=https://jobs.lever.co/acme/1", "redirect"),
    ("http://careers.acme.com/job/1", "unsafe"),  # not https
    ("https://203.0.113.9/apply", "unsafe"),
    ("https://greenhouse-careers.com/acme/apply", "unsafe"),  # look-alike
    ("https://jobs.lever.co.apply-now.xyz/acme", "unsafe"),
    ("https://xn--grenhouse-9db.io/x", "unsafe"),
    ("https://careers.acme.com/files/apply.apk", "unsafe"),
    ("https://forms.gle/abc123", "unsafe"),
    ("https://user@evil.com/jobs", "unsafe"),
])
def test_link_check(url, level):
    assert linksafety.check(url).level == level


def test_fee_requests_are_scams():
    assert linksafety.check("https://careers.acme.com/1", "Pay Rs 999 registration fee to confirm").level == "unsafe"


def test_redirects_are_followed_and_checked_again():
    assert linksafety.screen("https://bit.ly/x", resolve=lambda u: "https://jobs.lever.co/acme/1").url == \
        "https://jobs.lever.co/acme/1"
    assert linksafety.screen("https://bit.ly/x", resolve=lambda u: "https://evil.xyz/").level == "unsafe"
    assert linksafety.screen("https://bit.ly/x", resolve=lambda u: "https://bit.ly/y").level == "unsafe"

    def broken(url):
        raise OSError("down")
    assert linksafety.screen("https://bit.ly/x", resolve=broken).level == "unsafe"
    assert linksafety.resolve("https://www.google.com/url?q=https://jobs.lever.co/a/1") == "https://jobs.lever.co/a/1"


def test_telegram_jobs_with_unsafe_links_are_saved_skipped_and_never_opened(tmp_path, profile):
    t = Tracker(tmp_path)
    tg = dict(source="telegram", company="Acme", location="Bengaluru", description=JD,
              extra={"subject": "Telegram: Jobs group"})
    links = {"Software Engineer": "https://forms.gle/abc", "Backend Engineer": "https://bit.ly/acme",
             "Frontend Engineer": "https://careers.acme.com/2"}
    jobs = [sources.Job(title=title, url=url, **{**tg, "extra": dict(tg["extra"])}) for title, url in links.items()]
    counts = pipeline.ingest(t, profile, jobs, resolve=lambda u: "https://jobs.lever.co/acme/7")
    assert counts["unsafe"] == 1 and counts["new"] + counts["auto_skipped"] == 2
    unsafe = t.find_url("https://forms.gle/abc")
    assert unsafe["status"] == "skipped" and "Unsafe link" in unsafe["notes"]
    assert t.find_url("https://jobs.lever.co/acme/7")  # the short link is saved as where it really leads


def code_email(sender="no-reply@us.greenhouse-mail.io", minutes_ago=0, code="AB12CD34"):
    msg = EmailMessage()
    msg["From"], msg["Subject"] = sender, "Security code for your application"
    msg["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
    msg.set_content(f"Copy and paste this code into the security code field on your application: {code}\n")
    return msg.as_bytes()


def test_security_code_is_read_only_from_application_systems():
    since = datetime.now(timezone.utc) - timedelta(minutes=2)
    assert gmail.extract_code(code_email(), since) == "AB12CD34"
    assert gmail.extract_code(code_email(sender="friend@gmail.com"), since) is None  # other mail is ignored
    assert gmail.extract_code(code_email(minutes_ago=30), since) is None  # an old code is not reused
    waits = []
    got = gmail.wait_for_code(since, fetch=lambda: [] if not waits else [code_email()], sleep=waits.append,
                              timeout=60, poll=5)
    assert got == "AB12CD34" and waits == [5]
