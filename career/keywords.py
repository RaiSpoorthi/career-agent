"""Free, offline keyword matching between a job description and the profile."""

from __future__ import annotations

from dataclasses import dataclass, field

from career import taxonomy
from career.profile import CLAIMABLE, Profile


@dataclass
class KeywordMatch:
    found: dict[str, int]  # term -> mentions in the job description
    matched: list[str] = field(default_factory=list)  # work/project evidence
    partial: list[str] = field(default_factory=list)  # academic only
    missing: list[str] = field(default_factory=list)  # no evidence at all
    score: int = 0  # 0-100
    track: str = ""
    track_votes: dict[str, int] = field(default_factory=dict)

    @property
    def confidence(self) -> str:
        n = len(self.found)
        return "low" if n < 5 else "medium" if n < 12 else "high"


def _evidence_level(profile: Profile, term: str) -> str | None:
    """Best level of evidence the profile has for a taxonomy term."""
    names = {n.lower() for n in taxonomy.evidence_names(term)}
    best: str | None = None
    for skill in profile.skills():
        if skill["name"].lower() in names:
            if skill["level"] in CLAIMABLE:
                return skill["level"]
            best = best or skill["level"]
    for entry in profile.entries.values():
        if names & {s.lower() for s in entry.get("stack", [])}:
            return "project"
    return best


def match(profile: Profile, job_text: str) -> KeywordMatch:
    found = taxonomy.find_terms(job_text)
    result = KeywordMatch(found=found)
    for term in sorted(found, key=lambda t: (-found[t], t)):
        level = _evidence_level(profile, term)
        if level in CLAIMABLE:
            result.matched.append(term)
        elif level == "academic":
            result.partial.append(term)
        else:
            result.missing.append(term)

    # Terms mentioned more often weigh more (capped so one repeated word can't dominate).
    def weight(term: str) -> int:
        return min(found[term], 3)

    total = sum(weight(t) for t in found)
    if total:
        got = sum(weight(t) for t in result.matched) + 0.5 * sum(weight(t) for t in result.partial)
        result.score = round(100 * got / total)

    # Each track lists the skill categories it covers (`categories:` in baseline.yaml; default: its own
    # name). The track with the most matching mentions wins; on a tie, the first track in the file.
    votes = {name: 0 for name in profile.tracks}
    for term, count in found.items():
        cat = taxonomy.category(term)
        for name, spec in profile.tracks.items():
            if cat in (spec.get("categories") or [name]):
                votes[name] += min(count, 3)
    result.track_votes = votes
    result.track = max(votes, key=lambda name: votes[name])  # max keeps the first of equal values
    return result


def add_supported_skills(profile: Profile, content: dict, job_text: str) -> list[str]:
    """Put every skill the job asks for that you can prove into the resume's Skills section, in place.

    Raises exact keyword coverage honestly: only skills at work/project level (or tools in a job's or
    project's stack) are added to their own group; coursework-only skills go under "familiar". Nothing
    you lack is ever added. Returns the names added.
    """
    skills: dict[str, list[str]] = content.setdefault("skills", {})
    listed = {n.lower() for names in skills.values() for n in names}
    by_name = {s["name"].lower(): s for s in profile.skills()}
    stack = {n.lower(): n for e in profile.entries.values() for n in e.get("stack", [])}
    added: list[str] = []
    for term in match(profile, job_text).found:
        names = [n.lower() for n in taxonomy.evidence_names(term)]
        if any(n in listed for n in names):
            continue
        skill = next((by_name[n] for n in names if n in by_name), None)
        if skill and skill["level"] in CLAIMABLE:
            group = skill["group"] if skill["group"] in skills else next(iter(skills), skill["group"])
            name = skill["name"]
        elif skill and skill["level"] == "academic":
            group, name = "familiar", skill["name"]
        elif any(n in stack for n in names):
            name = stack[next(n for n in names if n in stack)]
            group = next(iter(skills), "tools")
        else:
            continue
        skills.setdefault(group, []).append(name)
        listed.add(name.lower())
        added.append(name)
    return added


def match_resume(job_text: str, resume_text: str) -> int:
    """% of the job's taxonomy terms (weighted by mentions) that literally appear in the resume text."""
    job_terms = taxonomy.find_terms(job_text)
    if not job_terms:
        return 0
    resume_terms = set(taxonomy.find_terms(resume_text))
    total = sum(min(c, 3) for c in job_terms.values())
    got = sum(min(c, 3) for t, c in job_terms.items() if t in resume_terms)
    return round(100 * got / total)
