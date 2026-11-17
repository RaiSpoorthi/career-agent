"""Answer bank for application forms.

Every answer comes from profile/baseline.yaml or from config/answers.yaml (things only you know,
such as current CTC). Nothing is generated or guessed: a question with no rule here stays
unanswered, and if the form requires it the agent stops and hands the application to you.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from career.fit import FitReport, home_country
from career.forms import expected_salary
from career.profile import Profile, personal_file

ANSWERS_FILE = "config/answers.yaml"
CONSENT = re.compile(r"privacy|consent|i agree|i acknowledge|i have read|terms|data protection|gdpr", re.I)
RESUME_FIELD = re.compile(r"resume|r[ée]sum[ée]|\bcv\b|curriculum", re.I)
COVER_FIELD = re.compile(r"cover\s*letter", re.I)


@dataclass
class Rule:
    key: str
    pattern: re.Pattern
    value: str


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*:✱]+", " ", text or "")).strip().lower()


def load_extra(path: Path | None = None) -> dict[str, Any]:
    path = path or personal_file(ANSWERS_FILE)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build(profile: Profile, job: dict[str, str], fit: FitReport, cover_note: str = "",
          extra: dict[str, Any] | None = None) -> list[Rule]:
    """Rules are tried in order; the first whose pattern is found in the field label wins."""
    c, p, data = profile.contact, profile.preferences, profile.data
    extra = extra if extra is not None else load_extra()
    first, _, last = c["name"].partition(" ")
    edu, now = data["education"][0], data["experience"][0]
    heard = {"linkedin-alert": "LinkedIn", "naukri-alert": "Naukri"}.get(job.get("source", ""),
                                                                        "Company careers page")
    rules: list[tuple[str, str, str]] = []

    # Your own answers come first so they can override anything below.
    for item in extra.get("answers") or []:
        if item.get("question") and item.get("answer") not in (None, ""):
            rules.append(("custom", str(item["question"]), str(item["answer"])))

    rules += [
        ("first_name", r"first name|given name|forename", first),
        ("last_name", r"last name|surname|family name", last),
        ("preferred_name", r"preferred (first )?name|what should we call you", first),
        ("email", r"e-?mail", c["email"]),
        ("phone", r"phone|mobile|contact number", c["phone"]),
        ("linkedin", r"linkedin", c["linkedin"]),
        ("github", r"github", c["github"]),
        ("portfolio", r"portfolio|website|personal site|other (url|link)",
         str(extra.get("portfolio") or c.get("portfolio") or "")),
        ("notice", r"notice period|how soon can you (join|start)|earliest start", f"{p['notice_period_days']} days"),
        ("sponsorship", r"sponsor", "Yes" if fit.abroad else "No"),
        ("work_auth", r"authori[sz]ed to work|legally (able|allowed|permitted|entitled) to work|right to work|"
                      r"eligible to work|work permit", "No" if fit.abroad else "Yes"),
        ("salary", r"(expected|desired|target).{0,20}(salary|ctc|compensation|pay)|salary expectation|"
                   r"compensation expectation", expected_salary(profile, fit)),
        ("heard", r"how did you (hear|find|learn)|where did you (hear|find)|source of application", heard),
        ("grad_year", r"graduat(ion|ed|ing).{0,12}(year|date)|year of (graduation|passing)", str(p["graduation_year"])),
        ("gpa", r"\bc?gpa\b|grade point", edu["score"].replace("GPA", "").strip()),
        ("school", r"school|university|college|institution", edu["school"]),
        ("degree", r"degree|qualification|field of study|discipline|major", edu["degree"]),
        ("company", r"current (company|employer|organi[sz]ation)|^company$|^employer$|^org", now["org"]),
        ("title", r"current (job )?title|current (role|position|designation)|^title$|job title", now["title"]),
        ("country", r"^country|country of residence|which country", home_country(profile)),
        ("location", r"location|city|where are you (based|located)|current address", c["location"]),
        ("full_name", r"full name|legal name|your name|^name$|^name\b", c["name"]),
    ]
    if cover_note:
        rules.append(("cover", r"cover letter|why (are you|do you want|would you|this role|us)|"
                               r"additional information|anything else|tell us about yourself", cover_note))
    # A rule with nothing to say (e.g. no portfolio) is left out, so that question stays unanswered
    # instead of being filled with something else.
    return [Rule(key, re.compile(pattern, re.I), value) for key, pattern, value in rules if str(value).strip()]


def answer_for(rules: list[Rule], label: str) -> Rule | None:
    text = normalize(label)
    if not text:
        return None
    return next((r for r in rules if r.pattern.search(text)), None)


def pick_option(options: list[str], value: str) -> int | None:
    """Index of the option that says the same thing as `value`, or None. Never picks a near miss."""
    want = normalize(value)
    cleaned = [normalize(o) for o in options]
    for test in (lambda o: o == want, lambda o: o.startswith(want + " ") or o.startswith(want + ","),
                 lambda o: len(want) > 3 and want in o):
        hits = [i for i, o in enumerate(cleaned) if o and test(o)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1 and test(cleaned[hits[0]]) and want in ("yes", "no"):
            return hits[0]
    return None
