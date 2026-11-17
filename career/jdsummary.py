"""A short, readable summary of a job description: experience asked, skills, what you'd do, pay.

Works offline from the posting's own section headings ("Responsibilities", "Requirements",
"Nice to have"...). When a packet has Gemini's job analysis, its skill lists are used as well.
Nothing here is invented: every line is taken from the posting or from that analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from career import fit as fitmod
from career import keywords
from career.profile import Profile

HEADINGS = {
    "duties": r"responsibilit|what you('| wi)ll (do|be doing|work on)|your role|the role|role overview|key duties|"
              r"day[- ]to[- ]day|what you will own|in this role|job description",
    "required": r"requirement|qualification|what you('| wi)ll (need|bring)|must[- ]have|who you are|about you|"
                r"what we('re| are) looking for|you (have|bring|should have)|skills|experience",
    "nice": r"nice[- ]to[- ]have|preferred|bonus|good to have|plus\b|desirable|added advantage",
    # sections that end the useful part: nothing under them is collected
    "stop": r"benefit|perks|what we offer|why (join|work)|our (culture|values)|equal (employment )?opportunit|"
            r"about (us|the company)|who we are|our mission|compensation and|accommodation",
}
CULTURE = re.compile(r"^you (are|act|thrive|love|embrace|bring energy)\b", re.I)
BULLET = re.compile(r"^\s*(?:[-*•·▪◦●○➤►✓✔]|\d{1,2}[.)])\s*")
MAX_ITEMS, MAX_LEN, MAX_PROSE = 6, 170, 200


@dataclass
class Summary:
    years: str = "not stated"
    pay: str = "not stated"
    location: str = ""
    remote: bool = False
    seniority: str = ""
    duties: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    nice: list[str] = field(default_factory=list)
    skills_have: list[str] = field(default_factory=list)
    skills_coursework: list[str] = field(default_factory=list)
    skills_missing: list[str] = field(default_factory=list)
    must_have_skills: list[str] = field(default_factory=list)   # from Gemini's analysis, when there is one
    nice_skills: list[str] = field(default_factory=list)

    @property
    def one_line(self) -> str:
        top = ", ".join((self.must_have_skills or self.skills_have + self.skills_missing)[:4])
        return " · ".join(p for p in (f"Experience: {self.years}", f"Pay: {self.pay}", f"Key skills: {top}" if top
                                      else "") if p)


def _heading(line: str) -> str | None:
    text = BULLET.sub("", line).strip().rstrip(":").strip()
    if not text or len(text) > 60 or text.endswith("."):
        return None
    for kind in ("stop", "nice", "duties", "required"):  # "nice to have" before the broader "required" phrases
        if re.search(HEADINGS[kind], text, re.I):
            return kind
    return None


def _clean(line: str) -> str:
    text = re.sub(r"\s+", " ", BULLET.sub("", line)).strip()
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 1].rsplit(" ", 1)[0] + "…"


def sections(text: str) -> dict[str, list[str]]:
    """Lines under each recognised heading, cleaned and capped."""
    out: dict[str, list[str]] = {"duties": [], "required": [], "nice": []}
    current: str | None = None
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        kind = _heading(raw)
        if kind:
            current = None if kind == "stop" else kind
            continue
        line = BULLET.sub("", raw).strip()
        # Requirements are short bullet lines; long paragraphs and "You are..." lines are company-culture prose,
        # '#' lines are tags.
        if (current and len(out[current]) < MAX_ITEMS and 12 < len(line) <= MAX_PROSE and not line.startswith("#")
                and not CULTURE.match(line)):
            out[current].append(_clean(raw))
    return out


def summarize(profile: Profile, text: str, location: str = "", title: str = "",
              analysis: dict[str, Any] | None = None) -> Summary:
    report = fitmod.check(profile, text, location, title)
    kw = keywords.match(profile, text)
    found = sections(text)
    jd = ((analysis or {}).get("model_output") or {}).get("step2_job_analysis") or {}
    return Summary(
        years=f"{report.years_required}+ years" if report.years_required is not None else "not stated",
        pay=report.salary, location=location or report.location, remote=report.remote,
        seniority=str(jd.get("seniority") or ""),
        duties=found["duties"] or [_clean(r) for r in (jd.get("responsibilities") or [])[:MAX_ITEMS]],
        required=found["required"], nice=found["nice"],
        skills_have=kw.matched[:12], skills_coursework=kw.partial[:8], skills_missing=kw.missing[:10],
        must_have_skills=[str(s) for s in (jd.get("required_skills") or [])[:10]],
        nice_skills=[str(s) for s in (jd.get("preferred_skills") or [])[:8]],
    )
