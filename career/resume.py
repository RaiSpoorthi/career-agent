"""Resume content model: base content per track, and layout for the renderers.

"Content" is the part an AI may propose changes to:
    {"track", "headline",
     "experience": [{"id", "bullets": [{"source_id", "text"}]}],
     "projects":   [{"id", "bullets": [{"source_id", "text"}]}],
     "skills":     {group: [skill names]}}
Everything else (contact, dates, titles, education, certifications) always
comes straight from baseline.yaml and can't be changed by the AI.
"""

from __future__ import annotations

from typing import Any

from career.profile import CLAIMABLE, Profile


def base_content(profile: Profile, track: str) -> dict[str, Any]:
    spec = profile.tracks[track]
    chosen = spec.get("bullets", {})

    def entry(entry_id: str) -> dict[str, Any]:
        ids = chosen.get(entry_id) or [b["id"] for b in profile.entries[entry_id]["bullets"]]
        return {"id": entry_id,
                "bullets": [{"source_id": b, "text": profile.bullet_text(b, track)} for b in ids]}

    skills = spec.get("skills") or {
        group: [s["name"] for s in profile.skills(CLAIMABLE) if s["group"] == group]
        for group in {s["group"] for s in profile.skills(CLAIMABLE)}
    }
    return {
        "track": track,
        "headline": spec["headline"],
        "experience": [entry(e) for e in spec["experience_order"]],
        "projects": [entry(p) for p in spec["project_order"]],
        "skills": {g: list(v) for g, v in skills.items()},
    }


def layout(profile: Profile, content: dict[str, Any], international: bool = False) -> dict[str, Any]:
    """Turn content into the document structure both renderers draw."""
    titles = profile.data["skill_group_titles"]
    experience = []
    for item in content["experience"]:
        src = profile.entries[item["id"]]
        experience.append({"title": src["title"], "org": src["org"], "dates": src["dates"],
                           "bullets": [b["text"] for b in item["bullets"]]})
    projects = []
    for item in content["projects"]:
        src = profile.entries[item["id"]]
        stack = [s for s in src["stack"] if s in _short_stack(src)]
        projects.append({"name": src["name"], "url": src.get("url") or "", "stack": stack,
                         "bullets": [b["text"] for b in item["bullets"]]})
    skills = [(titles.get(group, group.title()), names) for group, names in content["skills"].items() if names]
    education = [e for e in profile.data["education"] if not (international and e.get("drop_for_international"))]
    return {
        "track": content["track"],
        "contact": profile.contact,
        "headline": content["headline"],
        "experience": experience,
        "projects": projects,
        "skills": skills,
        "education": education,
        "certifications": extra_items(profile),
    }


def extra_items(profile: Profile) -> list[dict[str, Any]]:
    """Certifications then achievements, each as {name, kind, tracks, priority}.

    baseline.yaml may list an entry as a plain string or as {name, tracks, priority}.
    """
    items = []
    for kind, key in (("certification", "certifications"), ("achievement", "achievements")):
        for entry in profile.data.get(key) or []:
            entry = entry if isinstance(entry, dict) else {"name": entry}
            items.append({"name": str(entry["name"]), "kind": kind, "tracks": list(entry.get("tracks") or []),
                          "priority": entry.get("priority")})
    return items


def _short_stack(entry: dict[str, Any]) -> list[str]:
    """First few concrete tools of a project, for the right-hand stack label."""
    concepts = {"Infrastructure as Code", "DevSecOps", "Policy as code", "Unit testing", "IAM",
                "Least privilege", "Automation", "Machine learning", "Malware analysis", "Memory forensics",
                "Anomaly detection", "Threat Detection", "Web application security", "Penetration Testing",
                "SQL injection", "XSS", "OWASP Top 10", "CI/CD", "AWS"}
    return [s for s in entry.get("stack", []) if s not in concepts][:6]


def plain_text(doc: dict[str, Any]) -> str:
    """Flatten a laid-out resume for keyword scoring."""
    parts = [doc["headline"]]
    for e in doc["experience"]:
        parts += [e["title"], e["org"], *e["bullets"]]
    for p in doc["projects"]:
        parts += [p["name"], " ".join(p["stack"]), *p["bullets"]]
    for title, names in doc["skills"]:
        parts.append(f"{title}: {', '.join(names)}")
    parts += [e["degree"] for e in doc["education"]]
    parts += [c["name"] for c in doc["certifications"]]
    return "\n".join(parts)
