"""Keep a tailored resume's Skills section short and on target for the job.

Rules from baseline.yaml, applied last (after tailoring and after add_supported_skills):

* `preferences.languages: asked` - the "languages" group lists only the programming languages the job
  asks for; if it asks for none, only `preferences.default_language` (e.g. Python).
* `tracks.<name>.hide_categories: [security]` - on that track, skills of those taxonomy categories are
  left out unless the job explicitly asks for them; a headline naming one goes back to the track's own.
* `preferences.max_skills` / `max_per_group` - a short list: skills the job asks for come first, then
  the rest in the tailored order, until the limit. Coursework-only ("familiar") skills appear only
  when the job asks for them.

Only removes or keeps skills you already have; never adds anything.
"""

from __future__ import annotations

import re
from typing import Any

from career import taxonomy
from career.profile import Profile

LANGUAGE_GROUP = "languages"
FAMILIAR_GROUP = "familiar"  # coursework-only skills (the guard moves them here)


def skill_categories(name: str) -> set[str]:
    if name in taxonomy.TERMS:
        return {taxonomy.category(name)}
    return {taxonomy.category(t) for t in taxonomy.find_terms(name)}


def _asked(name: str, job_terms: set[str], job_text: str) -> bool:
    """Does the job ask for this skill (by its taxonomy term or, failing that, by its exact name)?"""
    terms = ({name} if name in taxonomy.TERMS else set()) | set(taxonomy.find_terms(name))
    if terms:
        return bool(terms & job_terms)
    return bool(re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w+#])", job_text, re.I))


def requested_skills(profile: Profile, requests: list[str]) -> list[str]:
    """Profile skills the user named in their own change requests ("add Python and SQL")."""
    text = " ".join(requests)
    return [s["name"] for s in profile.skills()
            if re.search(r"(?<![\w])" + re.escape(s["name"]) + r"(?![\w+#])", text, re.I)]


def focus_skills(profile: Profile, content: dict[str, Any], job_text: str, track: str,
                 pinned: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Trim content["skills"] (and the headline) in place. Returns the skill names removed.

    `pinned`: skills the user asked for in a change request. They are put back if missing and always kept,
    ahead of every rule here (the user's own request beats the defaults)."""
    spec = profile.tracks.get(track) or {}
    prefs = profile.preferences
    hide = set(spec.get("hide_categories") or [])
    skills: dict[str, list[str]] = content.setdefault("skills", {})
    listed = {n.lower() for names in skills.values() for n in names}
    for name in pinned:
        if name.lower() not in listed:
            level = profile.skill_level(name)
            group = FAMILIAR_GROUP if level == "academic" else next(
                (s["group"] for s in profile.skills() if s["name"] == name), "tools")
            skills.setdefault(group, []).append(name)
    job_terms = set(taxonomy.find_terms(job_text))
    asked = {n: _asked(n, job_terms, job_text) or n in pinned for names in skills.values() for n in names}
    before = [n for names in skills.values() for n in names]

    for group in list(skills):
        skills[group] = [n for n in skills[group]
                         if asked[n] or not ((hide and skill_categories(n) & hide) or group == FAMILIAR_GROUP)]
    if hide:
        headline = content.get("headline") or ""
        named = taxonomy.find_terms(headline)
        if spec.get("headline") and any(taxonomy.category(t) in hide and t not in job_terms for t in named):
            content["headline"] = spec["headline"]

    if prefs.get("languages") == "asked" and before and LANGUAGE_GROUP in skills:
        names = skills[LANGUAGE_GROUP]
        keep = [n for n in names if asked.get(n)]
        if not keep:
            default = str(prefs.get("default_language") or (names[:1] or [""])[0])
            have = {s["name"].lower(): s["name"] for s in profile.skills()}
            keep = [have[default.lower()]] if default.lower() in have else names[:1]
        skills[LANGUAGE_GROUP] = keep

    total, per_group = int(prefs.get("max_skills") or 0), int(prefs.get("max_per_group") or 0)
    if pinned:  # make room for what the user asked for instead of dropping a skill the job wants
        total = total and total + len(pinned)
        per_group = per_group and per_group + len(pinned)
    _cap(skills, asked, total, per_group, pinned)
    for group in [g for g, names in skills.items() if not names]:
        del skills[group]
    if FAMILIAR_GROUP in skills:  # coursework-only skills always come last
        skills[FAMILIAR_GROUP] = skills.pop(FAMILIAR_GROUP)
    after = {n for names in skills.values() for n in names}
    return [n for n in before if n not in after]


def _cap(skills: dict[str, list[str]], asked: dict[str, bool], total: int, per_group: int,
         pinned: list[str] | tuple[str, ...] = ()) -> None:
    """Keep at most `total` skills (and `per_group` per group): pinned, then asked-for ones, in their order."""
    if not total and not per_group:
        return
    order = [(g, n) for g, names in skills.items() for n in names]
    ranked = ([p for p in order if p[1] in pinned] + [p for p in order if asked.get(p[1]) and p[1] not in pinned]
              + [p for p in order if not asked.get(p[1])])
    chosen: set[tuple[str, str]] = set()
    count: dict[str, int] = {}
    for group, name in ranked:
        if total and len(chosen) >= total:
            break
        if per_group and count.get(group, 0) >= per_group:
            continue
        chosen.add((group, name))
        count[group] = count.get(group, 0) + 1
    for group in skills:
        skills[group] = [n for n in skills[group] if (group, n) in chosen]
