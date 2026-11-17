"""What people are usually paid for a job, from a Gemini web search, for the "expected salary" answer.

Gemini searches the web (Google Search grounding) for salaries for the role, level and city, and returns
a range with the pages it used. If search isn't available (free-tier limits, model support), it answers
from its own knowledge and the result says so. The answer you give on forms is never below your own floor
(career/forms.py), and the estimate is shown on the job page so you can check it.
"""

from __future__ import annotations

from typing import Any

from career.llm import JSONModel, LLMError
from career.profile import Profile

SYSTEM = ("You research salaries. Give realistic ranges for the exact role, seniority and city, from salary "
          "sites and recent postings. Answer with one JSON object only.")

PROMPT = """Find what people are typically paid for this job.
Role: {role}
Company: {company}
Location: {location}
Experience the job asks for: {years}
Candidate's level: {level}

Give the range that someone at the candidate's level is typically paid when hired into this role at this kind of
company and city: not the range for senior or experienced people. Look at salary data for this role, level and
city (for example AmbitionBox, Glassdoor, Levels.fyi, Naukri, LinkedIn salaries, company postings).
For jobs in India give lakhs per annum (unit "LPA"); elsewhere give annual pay in the local currency
(unit = its ISO code, e.g. "USD", "GBP", "EUR").
Answer JSON only:
{{"low": number, "high": number, "unit": "LPA or ISO currency code",
  "basis": "one sentence: which sources and which level this range is based on",
  "confidence": "high|medium|low"}}"""


def label(low: float, high: float, unit: str) -> str:
    if unit == "LPA":
        return f"{low:g}-{high:g} LPA" if high != low else f"{low:g} LPA"
    return f"{unit} {low:,.0f}-{high:,.0f}" if high != low else f"{unit} {low:,.0f}"


def _valid(answer: dict[str, Any]) -> dict[str, Any] | None:
    try:
        low, high = float(answer["low"]), float(answer["high"])
    except (KeyError, TypeError, ValueError):
        return None
    unit = str(answer.get("unit") or "").strip().upper()
    if unit in ("INR LPA", "LAKHS", "LAKH"):
        unit = "LPA"
    if not unit or low <= 0 or high < low or (unit == "LPA" and high > 300):
        return None
    return {"low": low, "high": high, "unit": unit, "label": label(low, high, unit),
            "basis": str(answer.get("basis") or "")[:300], "confidence": str(answer.get("confidence") or "")}


def candidate_level(profile: Profile) -> str:
    """`preferences.experience_level` if you wrote one, else a summary from your profile."""
    prefs = profile.preferences
    if prefs.get("experience_level"):
        return str(prefs["experience_level"])
    titles = [e.get("title", "") for e in profile.data.get("experience", [])]
    interns = sum("intern" in t.lower() for t in titles)
    kind = "internships" if interns == len(titles) and titles else "jobs/internships"
    return f"early career: graduation year {prefs.get('graduation_year', '?')}, {len(titles)} {kind}"


def estimate(llm: JSONModel, profile: Profile, role: str, company: str, location: str,
             years: str) -> dict[str, Any] | None:
    """{"low", "high", "unit", "label", "basis", "confidence", "searched", "sources"}, or None if unusable."""
    prompt = PROMPT.format(role=role, company=company, location=location or "not stated", years=years,
                           level=candidate_level(profile))
    searched, sources = False, []
    try:
        answer, sources = llm.generate_json_searched(SYSTEM, prompt)  # type: ignore[attr-defined]
        searched = True
    except (AttributeError, LLMError):
        try:
            answer = llm.generate_json(SYSTEM, prompt)
        except LLMError:
            return None
    result = _valid(answer)
    if result:
        result.update(searched=searched, sources=sources)
    return result
