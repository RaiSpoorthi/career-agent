"""Keep a tailored resume's Skills section on target for the job.

Two rules from baseline.yaml, applied last (after tailoring and after add_supported_skills):

* `preferences.languages: asked` - the "languages" group lists only the programming languages the job
  asks for; if it asks for none, only `preferences.default_language` (e.g. Python).
* `tracks.<name>.hide_categories: [security]` - on that track no skill of those taxonomy categories
  appears in Skills, and a headline that names one goes back to the track's own headline.

Only removes or keeps skills you already have; never adds anything.
"""

from __future__ import annotations

import re
from typing import Any

from career import taxonomy
from career.profile import Profile

LANGUAGE_GROUP = "languages"


def skill_categories(name: str) -> set[str]:
    if name in taxonomy.TERMS:
        return {taxonomy.category(name)}
    return {taxonomy.category(t) for t in taxonomy.find_terms(name)}


def _asked(name: str, job_terms: set[str], job_text: str) -> bool:
    if name in taxonomy.TERMS:
        return name in job_terms
    return bool(re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w+#])", job_text, re.I))


def focus_skills(profile: Profile, content: dict[str, Any], job_text: str, track: str) -> list[str]:
    """Trim content["skills"] (and the headline) in place. Returns the skill names removed."""
    spec = profile.tracks.get(track) or {}
    prefs = profile.preferences
    hide = set(spec.get("hide_categories") or [])
    skills: dict[str, list[str]] = content.get("skills") or {}
    removed: list[str] = []

    if hide:
        for group in list(skills):
            keep = [n for n in skills[group] if not (skill_categories(n) & hide)]
            removed += [n for n in skills[group] if n not in keep]
            skills[group] = keep
        headline = content.get("headline") or ""
        if any(taxonomy.category(t) in hide for t in taxonomy.find_terms(headline)) and spec.get("headline"):
            content["headline"] = spec["headline"]

    if prefs.get("languages") == "asked" and skills.get(LANGUAGE_GROUP):
        job_terms = set(taxonomy.find_terms(job_text))
        names = skills[LANGUAGE_GROUP]
        keep = [n for n in names if _asked(n, job_terms, job_text)]
        if not keep:
            default = str(prefs.get("default_language") or names[0])
            have = {s["name"].lower(): s["name"] for s in profile.skills()}
            keep = [have[default.lower()]] if default.lower() in have else names[:1]
        removed += [n for n in names if n not in keep]
        skills[LANGUAGE_GROUP] = keep

    for group in [g for g, names in skills.items() if not names]:
        del skills[group]
    return removed
