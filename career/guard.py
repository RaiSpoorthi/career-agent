"""Truthfulness guard.

Gemini proposes a tailored resume; this module decides what may stay. A rewritten
bullet survives only if it is a rephrasing of an existing baseline bullet:

  * it names a real source bullet from the same job/project;
  * every number in it already appears in that source bullet;
  * every tool/skill it mentions is in that source bullet or in that entry's stack,
    at work/project level (coursework-only skills are not allowed in bullets);
  * it doesn't add seniority claims ("led", "architected", ...) the source lacks.

Anything else is replaced by the original wording and logged. Skills must exist in
the profile; coursework-only skills are moved to "Familiar with (academic)".
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from career import taxonomy
from career.profile import CLAIMABLE, Profile
from career.resume import base_content

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
OVERCLAIMS = ["led", "lead", "leading", "spearheaded", "architected", "headed", "mentored", "managed a team",
              "owned", "single-handedly", "expert", "senior", "pioneered", "founded", "directed"]
MIN_LEN, MAX_LEN = 25, 260


@dataclass
class GuardResult:
    content: dict[str, Any]
    issues: list[str] = field(default_factory=list)
    kept_rewrites: int = 0
    reverted: int = 0


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(text or "")}


def _overclaims(text: str) -> set[str]:
    lowered = (text or "").lower()
    return {w for w in OVERCLAIMS if re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", lowered)}


def _entry_supports(profile: Profile, entry: dict[str, Any], term: str) -> bool:
    names = {n.lower() for n in taxonomy.evidence_names(term)}
    stack = {s.lower() for s in entry.get("stack", [])}
    if not names & stack:
        return False
    # The stack proves usage in this job/project, but coursework-only skills still don't count.
    levels = {profile.skill_level(n) for n in taxonomy.evidence_names(term)} - {None}
    return not levels or bool(levels & CLAIMABLE)


def check_bullet(profile: Profile, entry_id: str, source_id: str, text: str) -> list[str]:
    """Return the reasons a rewritten bullet is not acceptable (empty = OK)."""
    problems: list[str] = []
    if source_id not in profile.bullets or profile.bullet_owner.get(source_id) != entry_id:
        return [f"unknown source bullet '{source_id}' for {entry_id}"]
    text = (text or "").strip()
    if not (MIN_LEN <= len(text) <= MAX_LEN):
        problems.append(f"length {len(text)} outside {MIN_LEN}-{MAX_LEN} characters")
    sources = profile.all_bullet_texts(source_id)
    source_blob = " ".join(sources)

    new_numbers = _numbers(text) - _numbers(source_blob)
    if new_numbers:
        problems.append(f"adds numbers not in the source: {', '.join(sorted(new_numbers))}")

    entry = profile.entries[entry_id]
    source_terms = set(taxonomy.find_terms(source_blob))
    for term in taxonomy.find_terms(text):
        if term not in source_terms and not _entry_supports(profile, entry, term):
            problems.append(f"mentions '{term}', which this {entry_id} entry doesn't support")

    added = _overclaims(text) - _overclaims(source_blob)
    if added:
        problems.append(f"adds seniority claims: {', '.join(sorted(added))}")
    return problems


def check_free_text(profile: Profile, text: str, label: str) -> list[str]:
    """For headline / cover note: numbers and tools must exist somewhere in the profile."""
    problems = []
    all_text = " ".join(t for b in profile.bullets for t in profile.all_bullet_texts(b))
    all_text += " " + " ".join(e.get("score", "") for e in profile.data["education"])
    new_numbers = _numbers(text) - _numbers(all_text) - {"2026", "2022"}
    if new_numbers:
        problems.append(f"{label} adds numbers not in your profile: {', '.join(sorted(new_numbers))}")
    vocab = {v.lower() for v in profile.claimable_vocabulary()}
    for term in taxonomy.find_terms(text):
        if not {n.lower() for n in taxonomy.evidence_names(term)} & vocab:
            problems.append(f"{label} mentions '{term}', which isn't in your profile")
    added = _overclaims(text)
    if added:
        problems.append(f"{label} uses seniority claims: {', '.join(sorted(added))}")
    return problems


def _sanitize_entries(profile: Profile, kind: str, proposed: list[dict[str, Any]], base: list[dict[str, Any]],
                      track: str, result: GuardResult) -> list[dict[str, Any]]:
    base_by_id = {e["id"]: e for e in base}
    limits = {k: len(v) for k, v in profile.tracks[track].get("bullets", {}).items()}
    out: list[dict[str, Any]] = []
    seen_entries: set[str] = set()
    for item in proposed or []:
        entry_id = item.get("id")
        if entry_id not in base_by_id or entry_id in seen_entries:
            result.issues.append(f"{kind}: dropped unknown or duplicate entry '{entry_id}'")
            continue
        seen_entries.add(entry_id)
        bullets, used = [], set()
        for bullet in item.get("bullets") or []:
            source_id, text = bullet.get("source_id", ""), (bullet.get("text") or "").strip()
            if source_id in used:
                result.issues.append(f"{entry_id}: dropped duplicate of {source_id}")
                continue
            problems = check_bullet(profile, entry_id, source_id, text)
            if not problems:
                bullets.append({"source_id": source_id, "text": text})
                used.add(source_id)
                if text not in profile.all_bullet_texts(source_id):
                    result.kept_rewrites += 1
            elif source_id in profile.bullets and profile.bullet_owner.get(source_id) == entry_id:
                original = profile.bullet_text(source_id, track)
                bullets.append({"source_id": source_id, "text": original})
                used.add(source_id)
                result.reverted += 1
                result.issues.append(f"{source_id}: reverted to original ({'; '.join(problems)})")
            else:
                result.issues.append(f"{entry_id}: dropped bullet ({'; '.join(problems)})")
        cap = limits.get(entry_id, 99) + 1
        if len(bullets) > cap:
            result.issues.append(f"{entry_id}: trimmed to {cap} bullets")
            bullets = bullets[:cap]
        if not bullets:
            result.issues.append(f"{entry_id}: no valid bullets, using base wording")
            bullets = copy.deepcopy(base_by_id[entry_id]["bullets"])
        out.append({"id": entry_id, "bullets": bullets})
    for entry in base:  # never silently drop a job or project
        if entry["id"] not in seen_entries:
            if proposed:
                result.issues.append(f"{kind}: '{entry['id']}' was missing, restored base version")
            out.append(copy.deepcopy(entry))
    return out


def _sanitize_skills(profile: Profile, proposed: dict[str, list[str]], base: dict[str, list[str]],
                     result: GuardResult) -> dict[str, list[str]]:
    if not isinstance(proposed, dict) or not proposed:
        return copy.deepcopy(base)
    stack_names = {n.lower(): n for e in profile.entries.values() for n in e.get("stack", [])}
    out: dict[str, list[str]] = {g: [] for g in base}
    familiar: list[str] = []
    seen: set[str] = set()
    for group, names in proposed.items():
        target = group if group in out else None
        for name in names or []:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            level = profile.skill_level(name)
            canonical = next((s["name"] for s in profile.skills() if s["name"].lower() == key), None)
            if level is None and key in stack_names:
                level, canonical = "project", stack_names[key]
            if level in CLAIMABLE:
                seen.add(key)
                own = next((s["group"] for s in profile.skills() if s["name"].lower() == key), None)
                if target is None or (target == "languages" and own and own != "languages"):
                    # The skill's own group from the profile (created if this track's resume lacks it), never
                    # whichever group happens to be first: AWS must not land under "Languages".
                    match_group = next((s["group"] for s in profile.skills() if s["name"].lower() == key), None)
                    target_group = match_group or next((g for g in ("tools", "devtools") if g in out), "tools")
                    if target_group == "familiar":  # a stack tool listed as familiar in skills: keep it claimable
                        target_group = "tools"
                    out.setdefault(target_group, [])
                else:
                    target_group = target
                out[target_group].append(canonical)
            elif level == "academic":
                seen.add(key)
                familiar.append(canonical)
                result.issues.append(f"skill '{name}' is coursework-only: moved to 'Familiar with (academic)'")
            else:
                result.issues.append(f"skill '{name}' is not in your profile: removed")
    if familiar:
        out["familiar"] = familiar
    out = {g: v for g, v in out.items() if v}
    return out or copy.deepcopy(base)


def sanitize(profile: Profile, track: str, proposed: dict[str, Any]) -> GuardResult:
    base = base_content(profile, track)
    result = GuardResult(content={"track": track})
    proposed = proposed or {}

    headline = (proposed.get("headline") or "").strip()
    problems = check_free_text(profile, headline, "headline") if headline else ["empty"]
    if headline and not problems and len(headline) <= 110:
        result.content["headline"] = headline
    else:
        if headline:
            result.issues.append(f"headline reverted ({'; '.join(problems) or 'too long'})")
        result.content["headline"] = base["headline"]

    result.content["experience"] = _sanitize_entries(profile, "experience", proposed.get("experience"),
                                                     base["experience"], track, result)
    result.content["projects"] = _sanitize_entries(profile, "projects", proposed.get("projects"),
                                                   base["projects"], track, result)
    result.content["skills"] = _sanitize_skills(profile, proposed.get("skills"), base["skills"], result)
    return result
