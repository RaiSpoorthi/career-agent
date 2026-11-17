"""Pre-written answers for the questions application forms usually ask."""

from __future__ import annotations

from career.fit import FitReport, home_country, lpa_salary_floor, salary_floors
from career.profile import Profile

SYMBOL = {"USD": "$", "GBP": "£", "EUR": "€"}


def expected_salary(profile: Profile, fit: FitReport, market: dict | None = None) -> str:
    """Your expected-salary answer.

    With a market estimate (career/marketpay.py, a web search for this role and city): its midpoint, never
    below your own floor, "(open to discussion)". Otherwise `expected_salary` (your words, home-country jobs)
    or your salary floors.
    """
    from_market = _from_market(profile, market)
    if from_market:
        return from_market
    floors = salary_floors(profile)
    if fit.abroad:
        currency = fit.salary.split()[0] if fit.salary[:3] in SYMBOL else None
        if currency in floors:
            return (f"{currency} {floors[currency]:,}+ per year, in line with local market rates "
                    "(open to discussion)")
        if floors:
            each = " / ".join(f"{cur} {amount:,}" for cur, amount in floors.items())
            return f"In line with the local market rate for this role (at least the equivalent of {each} per year)"
        return "In line with the local market rate for this role (open to discussion)"
    if profile.preferences.get("expected_salary"):
        return str(profile.preferences["expected_salary"])
    if lpa_salary_floor(profile):
        return f"{lpa_salary_floor(profile):g} LPA or above (open to discussion)"
    return "Open to discussion"


def _from_market(profile: Profile, market: dict | None) -> str:
    if not market or not market.get("unit"):
        return ""
    unit, mid = market["unit"], (float(market["low"]) + float(market["high"])) / 2
    if unit == "LPA":
        amount = max(round(mid * 2) / 2, float(lpa_salary_floor(profile) or 0))
        return f"{amount:g} LPA (open to discussion)"
    amount = max(round(mid / 1000) * 1000, float(salary_floors(profile).get(unit) or 0))
    return f"{unit} {amount:,.0f} per year (open to discussion)"


def answers_markdown(profile: Profile, job: dict[str, str], fit: FitReport, cover_note: str = "",
                     market: dict | None = None) -> str:
    rows = answer_rows(profile, fit, market)
    lines = [f"# Application answers: {job.get('role', '')} @ {job.get('company', '')}", "",
             f"Link: {job.get('url') or '-'}", "",
             "| Question | Answer |", "|---|---|"]
    lines += [f"| {q} | {a} |" for q, a in rows]
    lines += ["", "## Why are you a good fit? (draft, checked against your profile)", "",
              cover_note or "_Not generated. Run `apply` with Gemini, or write 3-4 lines yourself._", "",
              "## Before you submit",
              "- Read the tailored resume once. You must be able to explain every line in an interview.",
              "- Upload `resume.pdf` unless the site asks for Word (use `resume.docx`).",
              "- After submitting: `python jobs.py status " + job.get("id", "<id>") + " applied`", ""]
    if market:
        lines += ["## Market pay (estimate)",
                  f"{market['label']}{' (web search)' if market.get('searched') else ' (Gemini, no web search)'}: "
                  f"{market.get('basis', '')}",
                  *[f"- [{s.get('title') or s['url']}]({s['url']})" for s in market.get("sources") or []], ""]
    if fit.reasons:
        lines += ["## Check first", *[f"- {r}" for r in fit.reasons], ""]
    return "\n".join(lines)


def answer_rows(profile: Profile, fit: FitReport, market: dict | None = None) -> list[tuple[str, str]]:
    """The usual application questions with your answers, from your profile (for answers.md and the dashboard)."""
    c = profile.contact
    p = profile.preferences
    edu = profile.data["education"][0]
    return [
        ("Full name", c["name"]),
        ("Email", c["email"]),
        ("Phone", c["phone"]),
        ("Current location", c["location"]),
        ("LinkedIn", c["linkedin"]),
        ("GitHub", c["github"]),
        ("Portfolio / website", c.get("portfolio") or "-"),
        ("Highest education", f"{edu['degree']}, {edu['school']} ({edu['dates']}), {edu['score']}"),
        ("Graduation year", str(p["graduation_year"])),
        ("Experience", "; ".join(f"{e['title']}, {e['org']} ({e['dates']})" for e in profile.data["experience"])),
        ("Notice period", f"{p['notice_period_days']} days"),
        ("Work authorization", p["work_authorization"]),
        ("Requires sponsorship?", "Yes" if fit.abroad else f"No (for roles in {home_country(profile)})"),
        ("Willing to relocate?", "Yes, with visa sponsorship" if fit.abroad
         else f"Based in {c['location']}; open to remote"),
        ("Expected salary", expected_salary(profile, fit, market)),
    ]
