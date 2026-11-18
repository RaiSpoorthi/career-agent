"""Fill and submit application forms in a real browser on your own computer (Playwright, free).

What it will and will not do:
  * It only opens public application forms hosted by Greenhouse, Lever, Ashby and Recruitee.
    Those forms need no account, so there is no account to restrict.
  * LinkedIn, Naukri, Indeed and other account-based sites are never opened. Those jobs go straight
    to the "Needs you" queue with the packet ready.
  * It answers only from the answer bank (career/answers.py). A required question with no answer,
    a captcha, or a submission that is not confirmed stops the run and hands the job to you.
  * It never tries to solve or get around a captcha.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from career import answers as ans
from career import fit as fitmod
from career import gmail, linksafety
from career import packet as pkt
from career.profile import Profile
from career.tracker import Tracker

BLOCKED_HOSTS = ("linkedin.com", "naukri.com", "indeed.com", "glassdoor.", "foundit.", "monster.", "shine.com",
                 "instahyre.com", "wellfound.com", "internshala.com")
SUPPORTED_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "recruitee.com")

CONFIRMED = re.compile(r"thank you for applying|thanks for applying|application (has been|was|is) (submitted|received|"
                       r"sent)|we('ve| have) received your application|successfully (submitted|applied)|"
                       r"your application has been", re.I)
CAPTCHA_FRAMES = ('iframe[src*="recaptcha"][src*="bframe"]', 'iframe[src*="hcaptcha.com"]',
                  'iframe[src*="challenges.cloudflare.com"]', 'iframe[title*="captcha" i]')
# Captcha checks often reject the agent's (automated) browser even when a person solves them. It is never
# disguised: you finish such forms in your own browser.
CAPTCHA_TIP = (" If the site then says it could not verify your application, use Apply in my browser: it opens "
               "the form in a normal tab, and the job page lists your answers to copy.")
SUBMIT_TEXT = re.compile(r"submit|apply|send application", re.I)

# Runs inside the page: finds every form field, its question text, and whether it is required.
COLLECT_JS = r"""
() => {
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const text = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const stop = p => ['FORM', 'BODY', 'MAIN'].includes(p.tagName);  // never borrow another question's label
  const ownLabel = el => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return text(l); }
    const wrap = el.closest('label'); if (wrap) return text(wrap);
    return '';
  };
  const question = el => {
    const al = el.getAttribute('aria-label'); if (al) return al;
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    const own = ownLabel(el); if (own) return own;
    let p = el.parentElement;
    for (let i = 0; i < 4 && p && !stop(p); i++, p = p.parentElement) {
      const l = p.querySelector('legend, label'); if (l && !l.contains(el)) return text(l);
    }
    return el.getAttribute('placeholder') || el.name || '';
  };
  const groupQuestion = el => {
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return text(lg); }
    let p = el.parentElement;
    for (let i = 0; i < 5 && p && !stop(p); i++, p = p.parentElement) {
      const l = p.querySelector('legend, [role=heading], label:not(:has(input))');
      if (l && !l.contains(el)) return text(l);
    }
    return '';
  };
  const required = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(label)
    || !!el.closest('.required, [data-required="true"]');
  const scope = document.querySelector('form') ? document.querySelectorAll('form') : [document.body];
  const out = [], groups = {};
  let n = 0;
  for (const root of scope) for (const el of root.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (type !== 'file' && !visible(el)) continue;
    if (el.disabled || el.readOnly && el.getAttribute('role') !== 'combobox') continue;
    // Dropdown widgets keep a hidden twin input only for validation; the visible combobox is the real field.
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex < 0) continue;
    const idx = String(n++); el.setAttribute('data-ca', idx);
    if (type === 'radio' || type === 'checkbox') {
      const q = groupQuestion(el), own = ownLabel(el) || el.value;
      const alone = !el.name || root.querySelectorAll('input[type=checkbox][name="' + CSS.escape(el.name) + '"]').length < 2;
      if (type === 'checkbox' && (!q || q === own || alone)) {
        out.push({idx, kind: 'checkbox', label: own, required: required(el, own), options: []}); continue;
      }
      const key = type + '|' + (el.name || q);
      if (!groups[key]) { groups[key] = {idx, kind: type === 'radio' ? 'radio' : 'checkboxes', label: q,
        required: required(el, q), options: []}; out.push(groups[key]); }
      groups[key].options.push({idx, text: own});
      continue;
    }
    const label = question(el);
    let kind = 'text', options = [];
    if (el.tagName === 'SELECT') { kind = 'select'; options = [...el.options].map(o => ({idx: o.value, text: text(o)})); }
    else if (type === 'file') kind = 'file';
    else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete')) kind = 'combo';
    out.push({idx, kind, label, required: required(el, label), options, name: el.name || el.id || '',
              big: el.tagName === 'TEXTAREA'});
  }
  return out;
}
"""


class Unavailable(RuntimeError):
    """The browser automation library is not installed."""


@dataclass
class ApplyResult:
    outcome: str  # submitted | completed-manual | handoff | manual-only | unavailable
    reason: str = ""
    filled: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def host_kind(url: str, supported: tuple[str, ...] = SUPPORTED_HOSTS) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return "none"
    if any(b in host for b in BLOCKED_HOSTS):
        return "blocked"
    return "supported" if any(host == s or host.endswith("." + s) or host == s.split(":")[0] for s in supported) \
        else "unsupported"


def _launch(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Unavailable("Browser automation is not installed. Run:  pip install playwright  and then  "
                          "python -m playwright install chromium") from exc
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=headless, args=[] if headless else ["--start-maximized"])
    except Exception as exc:
        pw.stop()
        raise Unavailable(f"Could not start the browser ({str(exc).splitlines()[0][:120]}). Run:  "
                          "python -m playwright install chromium") from exc
    return pw, browser


def _captcha_visible(page) -> bool:
    for selector in CAPTCHA_FRAMES:
        try:
            frames = page.locator(selector)
            for i in range(frames.count()):
                box = frames.nth(i).bounding_box()
                if box and box["width"] > 60 and box["height"] > 60:  # invisible badges are tiny or off-screen
                    return True
        except Exception:
            continue
    return False


def _confirmed(page) -> bool:
    try:
        if re.search(r"confirmation|thank", page.url, re.I):
            return True
        return bool(CONFIRMED.search(page.inner_text("body", timeout=2000)))
    except Exception:
        return False


def fill_form(page, rules: list[ans.Rule], resume: Path, tick_consent: bool) -> tuple[list[str], list[str]]:
    """Fill what the answer bank can answer. Returns (filled labels, required labels left unanswered)."""
    filled, unresolved = [], []
    # The resume goes in first: some forms (Greenhouse) redraw themselves after an upload, which clears
    # anything typed before it. Then the fields are collected again and filled.
    uploads = [f for f in page.evaluate(COLLECT_JS) if f["kind"] == "file"]
    for f in uploads:
        label = (f.get("label") or "").strip() or f.get("name", "") or "(unlabelled field)"
        probe = f"{label} {f.get('name', '')}"
        if ans.COVER_FIELD.search(probe):
            continue  # optional upload; the cover note goes in a text box when there is one
        if ans.RESUME_FIELD.search(probe) or f["required"]:
            try:
                page.locator(f'[data-ca="{f["idx"]}"]').set_input_files(str(resume))
                filled.append(label)
            except Exception:
                if f["required"]:
                    unresolved.append(label)
    if filled:
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(800)

    fields = page.evaluate(COLLECT_JS)
    # A separate country-code box gets "+91", so the phone box then gets the number alone.
    split_phone = any((r := ans.answer_for(rules, f.get("label") or "")) and r.key == "phone_code" for f in fields)
    for f in fields:
        label = (f.get("label") or "").strip() or f.get("name", "") or "(unlabelled field)"
        target = page.locator(f'[data-ca="{f["idx"]}"]')
        done = False
        try:
            if f["kind"] == "file":
                continue  # uploads were handled above
            elif f["kind"] == "checkbox":
                if ans.CONSENT.search(label) and tick_consent:
                    target.check()
                    done = True
            elif f["kind"] == "checkboxes":
                # Pick-several questions are never guessed. "How did you hear about us" is the one
                # exception: its answer is a fact (where the job came from), so tick that single option.
                rule = ans.answer_for(rules, label)
                choice = ans.pick_answer([o["text"] for o in f["options"]], rule) if rule and rule.key == "heard" \
                    else None
                if choice is not None:
                    page.locator(f'[data-ca="{f["options"][choice]["idx"]}"]').check()
                    done = True
            else:
                rule = ans.answer_for(rules, label)
                if rule and f["kind"] == "text":
                    if rule.key != "cover" or f.get("big"):
                        target.fill(rule.alts[0] if rule.key == "phone" and split_phone and rule.alts
                                    else rule.value)
                        done = True
                elif rule and f["kind"] in ("select", "radio"):
                    choice = ans.pick_answer([o["text"] for o in f["options"]], rule)
                    if choice is not None and f["kind"] == "select":
                        target.select_option(value=f["options"][choice]["idx"])
                        done = True
                    elif choice is not None:
                        page.locator(f'[data-ca="{f["options"][choice]["idx"]}"]').check()
                        done = True
                elif rule and f["kind"] == "combo":
                    done = _fill_combo(page, target, rule)
        except Exception:
            done = False
        if done:
            filled.append(label)
        elif f["required"]:
            unresolved.append(label)
    return filled, unresolved


def _fill_combo(page, target, rule: ans.Rule) -> bool:
    """Pick the option that matches, from the open list or by typing to search. No match means no answer."""
    # Only options on screen: pages keep other lists (e.g. the phone country codes) hidden in the page.
    options = page.locator('[role="option"]:visible')
    target.click()
    try:
        options.first.wait_for(state="visible", timeout=1500)
        choice = ans.pick_answer(options.all_inner_texts(), rule)
    except Exception:
        choice = None
    if choice is not None:
        options.nth(choice).click()
        return True
    for value in (rule.value, *rule.alts[:2]):  # each search can wait seconds, so try only a few wordings
        target.click()
        target.fill("")
        # Typed key by key: many dropdowns (React Select) ignore a value pasted in one go.
        target.press_sequentially(value.split(",")[0].strip(), delay=20)  # "Bengaluru, India" -> "Bengaluru"
        try:
            options.first.wait_for(state="visible", timeout=5000)
        except Exception:
            continue
        for _ in range(2):  # search results can arrive a moment after the first options show
            texts = options.all_inner_texts()[:15]
            choice = ans.pick_option(texts, value)
            if choice is None:
                choice = ans.pick_option(texts, value.split(",")[0])
            if choice is not None:
                options.nth(choice).click()
                return True
            page.wait_for_timeout(1000)
    target.fill("")
    target.press("Escape")
    return False


CODE_FIELD = ('input[id*="security" i], input[name*="security" i], input[autocomplete="one-time-code"], '
              'input[aria-label*="security code" i], input[aria-label*="verification code" i], '
              'input[id*="verification" i], input[name*="verification_code" i]')


def _code_boxes(page) -> list:
    """Visible security-code inputs (one box, or one box per character)."""
    try:
        boxes = page.locator(CODE_FIELD)
        return [boxes.nth(i) for i in range(boxes.count()) if boxes.nth(i).is_visible()]
    except Exception:
        return []


def _type_code(boxes: list, code: str) -> None:
    if len(boxes) > 1:  # one box per character
        for box, char in zip(boxes, code, strict=False):
            box.fill(char)
    else:
        boxes[0].fill(code)


def _email_code(email_code, since, root: Path, say, job_id: str) -> str | None:
    if email_code is None:
        if not gmail.mail_configured(root):
            return None
        email_code = lambda since: gmail.wait_for_code(since, root)  # noqa: E731
    say(f"  {job_id}: waiting for the security code email...")
    try:
        return email_code(since)
    except Exception as exc:
        say(f"  {job_id}: could not read the code from your email ({type(exc).__name__})")
        return None


def _submit(page) -> bool:
    buttons = page.locator('button[type="submit"], input[type="submit"], button')
    for i in range(buttons.count()):
        b = buttons.nth(i)
        try:
            name = (b.inner_text() or b.get_attribute("value") or "").strip()
            if b.is_visible() and SUBMIT_TEXT.search(name):
                b.click()
                return True
        except Exception:
            continue
    return False


def apply_to_job(tracker: Tracker, profile: Profile, job_id: str, *, mode: str = "auto",
                 supported: tuple[str, ...] = SUPPORTED_HOSTS, say=print, wait=time.sleep,
                 email_code=None) -> ApplyResult:
    """mode="auto": submit if everything required is answered, else hand off.
    mode="takeover": open a visible browser, fill the form, and leave Submit to you."""
    row = tracker.get(job_id)
    settings = tracker.settings()
    url = row["apply_url"] or row["url"]
    kind = host_kind(url, supported)

    def hand_off(reason: str, outcome: str = "handoff") -> ApplyResult:
        tracker.update(row["id"], status="awaiting-human", handoff_reason=reason,
                       attempts=str(int(row["attempts"] or 0) + 1))
        tracker.notify("needs-you", f"{row['role']} @ {row['company']}: {reason}", row["id"])
        say(f"  {row['id']} needs you: {reason}")
        return ApplyResult(outcome, reason)

    if not row["output_dir"] or not (tracker.root / row["output_dir"] / "resume.pdf").exists():
        return ApplyResult("handoff", "No packet yet: generate it first")
    if kind == "none":
        return hand_off("No application link saved. Open the listing and apply with the packet.", "manual-only")
    if kind == "blocked":
        return hand_off("This site needs your account, so it is never automated. Open the listing and apply "
                        "with the packet.", "manual-only")
    safety = linksafety.check(url)
    if safety.level != linksafety.OK:
        return hand_off(f"Link not opened for safety: {safety.reason}. Open it yourself only if you trust it.",
                        "manual-only")
    if kind == "unsupported" and mode == "auto":
        return hand_off("This application portal is not one the agent fills automatically. Press Take over.",
                        "manual-only")

    folder = tracker.root / row["output_dir"]
    resume = folder / f"{profile.contact['name'].replace(' ', '_')}_Resume.pdf"
    shutil.copyfile(folder / "resume.pdf", resume)
    text = tracker.jd_text(row["id"])
    fit = fitmod.check(profile, text, row["location"], row["role"])
    rules = ans.build(profile, row, fit, pkt.cover_note(tracker, row), market=pkt.market_pay(tracker, row))

    # CAREER_AGENT_HEADLESS=1 forces a hidden browser (used by the tests; a takeover needs a visible one).
    headless = (settings["headless"] == "on" and mode == "auto") or os.environ.get("CAREER_AGENT_HEADLESS") == "1"
    pw, browser = _launch(headless)
    try:
        # A visible browser follows the real window size: a fixed page size can be taller than the screen
        # (with Windows display scaling), hiding the bottom of the form or a captcha's Verify button.
        page = browser.new_page(viewport={"width": 1280, "height": 900}) if headless else browser.new_page(
            no_viewport=True)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector("form input, form textarea", timeout=15000)
        except Exception:
            # Some boards show the description first with an Apply button that reveals the form.
            opener = page.get_by_role("link", name=re.compile(r"apply", re.I)).or_(
                page.get_by_role("button", name=re.compile(r"apply", re.I)))
            if opener.count():
                opener.first.click()
                page.wait_for_selector("form input, form textarea", timeout=15000)
        # Let the page finish loading its scripts first: React forms (Greenhouse) reset anything typed or
        # uploaded before they are ready, so early answers silently vanish.
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        filled, unresolved = fill_form(page, rules, resume, settings["tick_consent_boxes"] == "on")
        tracker.update(row["id"], apply_method=f"browser:{urlparse(url).hostname}")
        tracker.log(f"form filled: {len(filled)} field(s); unanswered required: {unresolved or 'none'}", row["id"])
        try:
            page.screenshot(path=str(folder / "form_filled.png"), full_page=True)
        except Exception:
            pass

        if mode == "takeover":
            minutes = int(settings["handoff_wait_minutes"] or 20)
            note = ("Still to answer: " + "; ".join(unresolved)) if unresolved else "Everything I can answer is filled"
            say(f"  Browser is open for {row['id']}. {note}. Check it and press Submit. "
                f"I'll wait up to {minutes} minutes.")
            deadline = time.time() + minutes * 60
            while time.time() < deadline and not page.is_closed():
                if _confirmed(page):
                    tracker.update(row["id"], status="completed-manual", handoff_reason="")
                    say(f"  {row['id']}: submission confirmed. Marked completed manually.")
                    return ApplyResult("completed-manual", "", filled, unresolved)
                wait(2)
            reason = row["handoff_reason"] or "Opened for you, but no confirmation page was seen."
            tracker.update(row["id"], status="awaiting-human", handoff_reason=reason)
            say(f"  {row['id']}: no confirmation seen. If you did submit, press 'I submitted it'.")
            return ApplyResult("handoff", reason, filled, unresolved)

        if unresolved:
            return _with(hand_off("Required question(s) I have no answer for: " + "; ".join(unresolved[:6])),
                         filled, unresolved)
        if _captcha_visible(page):
            return _with(hand_off("The form shows a captcha." + CAPTCHA_TIP), filled, unresolved)
        submitted_at = gmail.now()
        if not _submit(page):
            return _with(hand_off("Could not find the Submit button."), filled, unresolved)
        code_tried = False
        for _ in range(15):
            wait(2)
            boxes = _code_boxes(page)
            if boxes and not code_tried and not _confirmed(page):
                # The form emailed you a security code (Greenhouse does this). Read it from mail sent by the
                # application system itself, enter it and submit again.
                code_tried = True
                code = _email_code(email_code, submitted_at, tracker.root, say, row["id"])
                if not code:
                    return _with(hand_off("The form emailed you a security code. Enter it from your inbox and "
                                          "press Submit (Take over)." + ("" if gmail.mail_configured(tracker.root)
                                          else " Connect Gmail (README) and the agent will do this for you.")),
                                 filled, unresolved)
                _type_code(boxes, code)
                _submit(page)
                continue
            if _confirmed(page):
                try:
                    page.screenshot(path=str(folder / "submitted.png"), full_page=True)
                except Exception:
                    pass
                tracker.update(row["id"], status="applied-auto", attempts=str(int(row["attempts"] or 0) + 1))
                say(f"  {row['id']}: submitted and confirmed.")
                return ApplyResult("submitted", "", filled, unresolved)
            if _captcha_visible(page):
                return _with(hand_off("A captcha appeared when submitting." + CAPTCHA_TIP), filled, unresolved)
        return _with(hand_off("Submit was pressed but no confirmation appeared. Check whether it went through "
                              "before applying again."), filled, unresolved)
    except Unavailable:
        raise
    except Exception as exc:
        return hand_off(f"The form could not be opened or filled ({type(exc).__name__}: {str(exc)[:90]}).")
    finally:
        try:
            browser.close()
        finally:
            pw.stop()


def open_packet_folder(tracker: Tracker, job_id: str) -> str:
    """Open the job's packet folder (File Explorer on Windows) so you can drag resume.pdf into a form.

    Used with "Apply in my browser": a plain link to the form, opened in your own browser, for sites that
    reject the agent's browser (for example "error verifying your application" after a captcha).
    """
    row = tracker.get(job_id)
    folder = tracker.root / row["output_dir"] if row["output_dir"] else None
    if not folder or not folder.exists():
        return "This job has no packet yet. Generate it first."
    _open_folder(folder)
    tracker.update(row["id"], apply_method="own-browser")
    return f"Opened {folder.name}: drag resume.pdf into the application form."


def _open_folder(folder: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(folder)  # Windows: opens File Explorer
    else:
        import webbrowser
        webbrowser.open(folder.as_uri())


def _with(result: ApplyResult, filled: list[str], unresolved: list[str]) -> ApplyResult:
    result.filled, result.unresolved = filled, unresolved
    return result
