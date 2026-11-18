"""Screen job links from Telegram groups (and other unvetted posts) before anything opens them.

A link is "ok", "redirect" (a shortener or redirect that hides where it goes) or "unsafe". Redirects are
resolved with plain HTTP requests (no browser, no page scripts, nothing downloaded or run) and the
destination is checked again; if it can't be seen, the link counts as unsafe. Unsafe jobs are saved as
skipped with the reason, so you can see them, but the agent never opens them in a browser.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

OK, REDIRECT, UNSAFE = "ok", "redirect", "unsafe"

SHORTENERS = ("bit.ly", "bitly.com", "tinyurl.com", "t.co", "rb.gy", "cutt.ly", "is.gd", "goo.gl", "shorturl.at",
              "tiny.cc", "ow.ly", "buff.ly", "rebrand.ly", "linktr.ee", "artha.link", "lnkd.in", "shorte.st",
              "adf.ly", "s.id", "v.gd", "short.gy", "surl.li")
FORMS = ("forms.gle", "docs.google.com/forms", "forms.office.com", "typeform.com", "jotform.com", "tally.so")
RISKY_TLDS = (".xyz", ".top", ".click", ".icu", ".tk", ".ml", ".ga", ".cf", ".gq", ".buzz", ".rest", ".zip",
              ".mov", ".cam", ".loan", ".work", ".country", ".kim", ".men", ".gdn", ".bid")
FILES = re.compile(r"\.(apk|exe|msi|bat|cmd|scr|zip|rar|7z|dmg|js|vbs|jar|iso|ps1|lnk)$", re.I)
# Brand names that phishing pages imitate -> the real domains.
BRANDS = {"greenhouse": ("greenhouse.io",), "lever": ("lever.co",), "ashby": ("ashbyhq.com",),
          "workday": ("myworkdayjobs.com", "myworkdaysite.com", "workday.com"), "linkedin": ("linkedin.com",),
          "naukri": ("naukri.com",), "google": ("google.com", "google.co.in", "goo.gl")}
SCAM_TEXT = re.compile(r"registration fee|training fee|joining fee|processing fee|security deposit|refundable deposit|"
                       r"pay (rs\.?|₹|inr)\s?\d|pay .{0,20}to (apply|register|confirm)|earn .{0,25}(daily|per day|"
                       r"per hour) from home|whatsapp (me|us|hr) (your|for)", re.I)


@dataclass
class Verdict:
    level: str
    reason: str = ""
    url: str = ""  # where the link really goes (after resolving a redirect)


def _host_is(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def check(url: str, text: str = "") -> Verdict:
    """Offline check of one link (and the post it came with). Never contacts the site."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if SCAM_TEXT.search(text or ""):
        return Verdict(UNSAFE, "The post asks for a fee or deposit: a common job scam", url)
    if parsed.scheme not in ("http", "https") or not host:
        return Verdict(UNSAFE, "Not a web link", url)
    if parsed.username or parsed.password or "@" in parsed.netloc:
        return Verdict(UNSAFE, "The link hides its real address (user@host trick)", url)
    try:
        if not ipaddress.ip_address(host).is_loopback:  # this computer (practice forms) is fine
            return Verdict(UNSAFE, "The link is a bare IP address, not a company site", url)
        return Verdict(OK, "", url)
    except ValueError:
        pass
    if "xn--" in host:
        return Verdict(UNSAFE, "The address uses look-alike letters (punycode)", url)
    if host.endswith(RISKY_TLDS):
        return Verdict(UNSAFE, f"The domain ending ({host.rsplit('.', 1)[-1]}) is common in scams", url)
    if FILES.search(parsed.path):
        return Verdict(UNSAFE, "The link downloads a file instead of opening a job page", url)
    for brand, real in BRANDS.items():
        if re.search(rf"(^|[.-]){brand}([.-]|$)", host) and not _host_is(host, real):
            return Verdict(UNSAFE, f"Looks like {brand} but is not its real site ({host})", url)
    if any(f in f"{host}{parsed.path}" for f in FORMS):
        return Verdict(UNSAFE, "A form outside any real application system: it may just collect your details", url)
    if _host_is(host, SHORTENERS) or (_host_is(host, ("google.com",)) and parsed.path == "/url"):
        return Verdict(REDIRECT, "A short or redirect link that hides where it goes", url)
    if parsed.scheme != "https":
        return Verdict(UNSAFE, "Not a secure (https) link", url)
    return Verdict(OK, "", url)


def resolve(url: str, timeout: int = 10, max_hops: int = 5) -> str:
    """Where a redirect link leads, read from the redirect headers only (no page content is loaded)."""
    target = parse_qs(urlparse(url).query).get("q") or parse_qs(urlparse(url).query).get("url")
    if target and urlparse(url).path == "/url":  # google.com/url?q=<target>
        return target[0]

    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoFollow)
    for _ in range(max_hops):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        try:
            with opener.open(request, timeout=timeout):
                return url  # no further redirect
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if 300 <= exc.code < 400 else None
            if not location:
                return url
            url = urllib.parse.urljoin(url, location)
            if urlparse(url).scheme not in ("http", "https"):
                return url
    return url


def screen(url: str, text: str = "", resolve=resolve) -> Verdict:
    """check(), and for a redirect link, check where it really goes."""
    verdict = check(url, text)
    if verdict.level != REDIRECT:
        return verdict
    try:
        final = resolve(url)
    except Exception as exc:
        return Verdict(UNSAFE, f"Could not see where the short link leads ({type(exc).__name__})", url)
    again = check(final, text)
    if again.level == REDIRECT:
        return Verdict(UNSAFE, "The short link does not reveal where it leads", url)
    return Verdict(again.level, again.reason, final)
