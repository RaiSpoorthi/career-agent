"""Free, offline checks of a job against your hard rules (`preferences` in baseline.yaml).

Location, visa sponsorship, salary floor and experience level are read from
the job text with simple patterns. Anything the text does not state is reported
as "not stated", never guessed.

Location rules use `home_country` (default: the last part of contact.location) and its cities:
a built-in list for India, or `home_places` in your preferences for any other country.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from career.profile import Profile

APPLY, REVIEW, SKIP = "APPLY", "REVIEW", "SKIP"

KNOWN_HOME_PLACES = {
    "india": ["bengaluru", "bangalore", "hyderabad", "pune", "chennai", "mumbai", "delhi", "gurgaon",
              "gurugram", "noida", "kolkata", "ahmedabad", "kochi", "coimbatore", "mysore", "mysuru",
              "mangalore", "mangaluru", "india"],
}
ABROAD_PLACES = ["united states", "usa", "u.s.", "united kingdom", "uk", "london", "germany", "berlin",
                 "munich", "netherlands", "amsterdam", "ireland", "dublin", "canada", "toronto",
                 "australia", "sydney", "singapore", "dubai", "uae", "abu dhabi", "france", "paris",
                 "europe", "emea", "new york", "san francisco", "seattle", "austin", "poland", "spain",
                 "sweden", "denmark", "switzerland", "japan", "luxembourg", "belgium", "india"]
US_NAMES = {"united states", "usa", "us", "u.s.", "united states of america"}

SPONSOR_YES = [r"visa sponsorship (is )?(available|provided|offered)", r"(we|will) sponsor",
               r"sponsorship (is )?(available|provided|offered)", r"relocation (and|&) visa",
               r"visa support", r"work permit support", r"sponsor(ing)? (your |a )?visa"]
SPONSOR_NO = [r"(no|not|unable to|cannot|can't|do not|don't|will not|won't)\s+(provide\s+|offer\s+)?"
              r"(visa\s+)?sponsor", r"without (visa )?sponsorship", r"must (be|have) (legally )?authori[sz]ed to work",
              r"must have (the )?right to work", r"(citizens?|permanent residents?) only",
              r"security clearance", r"must be (a )?(us|u\.s\.|uk|eu) (citizen|national)"]

REMOTE = re.compile(r"(?<![\w])(remote|work from home|wfh|anywhere)(?![\w])", re.I)
LPA = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|to|–)?\s*(\d+(?:\.\d+)?)?\s*(?:lpa|lakhs?|l\.p\.a)", re.I)
MONEY = re.compile(r"(?P<cur>[$£€])\s?(?P<a>\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])"
                   r"(?:\s*(?:-|to|–)\s*[$£€]?\s?(?P<b>\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK]))?")
YEARS = re.compile(r"(\d+)\s*(?:\+|-\s*\d+|to\s*\d+)?\s*(?:\+\s*)?years?(?:\s+of)?\s+"
                   r"(?:relevant\s+|professional\s+|industry\s+|hands-on\s+|work\s+)?experience", re.I)
CUR_CODE = {"$": "USD", "£": "GBP", "€": "EUR"}
# Titles that are above entry level. Checked against the job title only, never the description.
SENIOR_TITLE = re.compile(r"(?<![\w])(senior|sr\.?|staff|principal|lead|manager|director|head|vp|vice president|"
                          r"architect|distinguished|chief)(?![\w])", re.I)


@dataclass
class FitReport:
    verdict: str = APPLY
    location: str = "not stated"
    abroad: bool = False
    remote: bool = False
    sponsorship: str = "not stated"  # yes / no / not stated
    salary: str = "not stated"
    salary_ok: bool | None = None
    years_required: int | None = None
    reasons: list[str] = field(default_factory=list)

    def downgrade(self, verdict: str, reason: str) -> None:
        order = {APPLY: 0, REVIEW: 1, SKIP: 2}
        if order[verdict] > order[self.verdict]:
            self.verdict = verdict
        self.reasons.append(reason)


def _has(text: str, word: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", text, re.I) is not None


def _amount(raw: str) -> int:
    raw = raw.replace(",", "").replace(" ", "")
    return int(raw[:-1]) * 1000 if raw[-1] in "kK" else int(raw)


def salary_floors(profile: Profile) -> dict[str, int]:
    """Minimum annual pay by currency (USD/GBP/EUR). `salary_min`; older profiles: `international_salary_min`."""
    prefs = profile.preferences
    return prefs.get("salary_min") or prefs.get("international_salary_min") or {}


def lpa_salary_floor(profile: Profile) -> float | None:
    """Minimum pay in lakhs per annum, for Indian postings (`salary_min_lpa`; older: `india_salary_min_lpa`)."""
    prefs = profile.preferences
    return prefs.get("salary_min_lpa", prefs.get("india_salary_min_lpa"))


def home_country(profile: Profile) -> str:
    return str(profile.preferences.get("home_country") or profile.contact["location"].split(",")[-1].strip())


def _home_places(profile: Profile) -> list[str]:
    country = home_country(profile).lower()
    places = [p.lower() for p in profile.preferences.get("home_places") or KNOWN_HOME_PLACES.get(country, [])]
    return places + ([country] if country not in places else [])


def check(profile: Profile, text: str, location_hint: str = "", title: str = "") -> FitReport:
    prefs = profile.preferences
    country = home_country(profile)
    home_places = _home_places(profile)
    foreign = [p for p in ABROAD_PLACES if p not in home_places]
    us_is_foreign = country.lower() not in US_NAMES
    ok_locations = [p.lower() for p in prefs.get("ok_locations", [])]
    report = FitReport()
    blob = f"{location_hint}\n{text}"
    lowered = blob.lower()

    # ---- location ----
    report.remote = bool(REMOTE.search(blob))
    home = [p for p in home_places if _has(lowered, p)]
    abroad = [p for p in foreign if _has(lowered, p)]
    if us_is_foreign and re.search(r"(?<![\w])(US|U\.S\.)(?![\w])", blob):  # case-sensitive: skip "us"
        abroad.append("us")
    hint = location_hint.lower()
    if hint:  # an explicit location wins over places mentioned in the description body
        home = [p for p in home_places if _has(hint, p)] or home
        hint_abroad = [p for p in foreign if _has(hint, p)]
        if us_is_foreign and re.search(r"(?<![\w])(US|U\.S\.)(?![\w])", location_hint):
            hint_abroad.append("us")
        abroad = hint_abroad or ([] if any(_has(hint, p) for p in home_places) else abroad)
    ok_local = any(p in ok_locations for p in home)
    report.abroad = bool(abroad) and not ok_local
    report.location = ", ".join(sorted(set(home + abroad))) or ("remote" if report.remote else "not stated")

    if report.remote or ok_local:
        pass
    elif report.abroad:
        report.reasons.append(f"Based outside {country}: needs visa sponsorship")
    elif home:
        wanted = "/".join(p.title() for p in ok_locations) or "your locations"
        report.downgrade(REVIEW, f"Located in {', '.join(home)}, not {wanted}")
    else:
        report.downgrade(REVIEW, "Location not stated")

    # ---- sponsorship ----
    if any(re.search(p, lowered) for p in SPONSOR_NO):
        report.sponsorship = "no"
    elif any(re.search(p, lowered) for p in SPONSOR_YES):
        report.sponsorship = "yes"
    if report.abroad and report.remote:
        report.downgrade(REVIEW, f"Remote role at a foreign employer: confirm they hire people based in {country}")
    elif report.abroad and prefs.get("needs_visa_sponsorship_abroad"):
        if report.sponsorship == "no":
            report.downgrade(SKIP, "Abroad and the posting says no visa sponsorship")
        elif report.sponsorship == "not stated":
            report.downgrade(REVIEW, "Abroad and sponsorship is not mentioned: confirm before applying")

    # ---- salary ----
    lpa = LPA.search(blob)
    money = MONEY.search(blob)
    lpa_floor = lpa_salary_floor(profile)
    if lpa:
        low, high = float(lpa.group(1)), float(lpa.group(2) or lpa.group(1))
        report.salary = f"{low:g}-{high:g} LPA" if high != low else f"{low:g} LPA"
        if lpa_floor:
            report.salary_ok = high >= lpa_floor
            if not report.salary_ok:
                report.downgrade(SKIP, f"Pay {report.salary} is below your {lpa_floor} LPA floor")
    elif money:
        cur = CUR_CODE[money.group("cur")]
        low = _amount(money.group("a"))
        high = _amount(money.group("b")) if money.group("b") else low
        report.salary = f"{cur} {low:,}-{high:,}" if high != low else f"{cur} {low:,}"
        floor = salary_floors(profile).get(cur)
        if floor and high < 5000:  # hourly/monthly figures: don't judge
            report.salary_ok = None
        elif floor:
            report.salary_ok = high >= floor
            if not report.salary_ok:
                report.downgrade(SKIP, f"Pay {report.salary} is below your {cur} {floor:,} floor")

    # ---- experience ----
    years = [int(m.group(1)) for m in YEARS.finditer(blob) if int(m.group(1)) < 30]
    if years:
        # The first figure is nearly always the headline requirement. Later, smaller figures
        # ("2 years with Terraform") must not hide it, so never go below the first one.
        report.years_required = max(years[0], min(years))
        limit = prefs.get("max_years_required", 2)
        if report.years_required > limit + 2:
            report.downgrade(SKIP, f"Asks for {report.years_required}+ years of experience")
        elif report.years_required > limit:
            report.downgrade(REVIEW, f"Asks for {report.years_required}+ years: a stretch, apply only if strong match")
    senior = SENIOR_TITLE.search(title or "")
    if senior:
        report.downgrade(SKIP, f"Title says '{senior.group(1)}': above entry level")
    return report
