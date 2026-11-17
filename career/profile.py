"""Load the master profile (profile/baseline.yaml) and expose lookups.

Personal files (your profile, form answers, job sources) are not in git. Each has a committed
`*.example.yaml` next to it; `python jobs.py init` copies the examples for you to edit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE_FILE = "profile/baseline.yaml"
# Examples mode: set this to a project folder and the personal files in it are ignored in favour of
# the committed examples. The tests set it to this checkout, so they never read your details;
# files in any other folder (a test's temporary copy of the project) are still used.
EXAMPLES_ENV = "CAREER_AGENT_EXAMPLES"

CLAIMABLE = {"work", "project"}


def using_examples() -> bool:
    return bool(os.environ.get(EXAMPLES_ENV))


def example_of(path: Path) -> Path:
    return path.with_name(f"{path.stem}.example{path.suffix}")


def _yours(path: Path) -> bool:
    """The file exists and examples mode isn't hiding it."""
    hidden = os.environ.get(EXAMPLES_ENV)
    return path.exists() and not (hidden and Path(hidden).resolve() in path.resolve().parents)


def personal_file(rel: str, root: Path = ROOT) -> Path:
    """Your copy of a personal config file, or its committed example if you have none yet."""
    path = root / rel
    return path if _yours(path) else example_of(path)


def profile_path(root: Path = ROOT) -> Path:
    """Your profile. Outside examples mode it never silently falls back to the sample person."""
    path = root / PROFILE_FILE
    if _yours(path):
        return path
    if using_examples():
        return example_of(path)
    raise FileNotFoundError(f"No profile yet ({PROFILE_FILE}). Run:  python jobs.py init  and then fill it in.")


@dataclass
class Profile:
    data: dict[str, Any]
    bullets: dict[str, dict[str, Any]] = field(default_factory=dict)  # bullet id -> bullet
    bullet_owner: dict[str, str] = field(default_factory=dict)  # bullet id -> entry id
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)  # entry id -> experience/project

    @classmethod
    def load(cls, path: Path | str | None = None) -> Profile:
        path = Path(path) if path else profile_path()
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data.get("sample") and not using_examples():
            raise ValueError(f"{path.name} still describes the sample person. Replace it with your own details "
                             "and delete the `sample: true` line, then try again.")
        profile = cls(data=data)
        for entry in data.get("experience", []) + data.get("projects", []):
            profile.entries[entry["id"]] = entry
            for bullet in entry.get("bullets", []):
                if bullet["id"] in profile.bullets:
                    raise ValueError(f"Duplicate bullet id in profile: {bullet['id']}")
                profile.bullets[bullet["id"]] = bullet
                profile.bullet_owner[bullet["id"]] = entry["id"]
        profile.validate()
        return profile

    def validate(self) -> None:
        for name, track in self.data["tracks"].items():
            for entry_id in track["experience_order"] + track["project_order"]:
                if entry_id not in self.entries:
                    raise ValueError(f"Track '{name}' references unknown entry '{entry_id}'")
            for entry_id, ids in track.get("bullets", {}).items():
                for bullet_id in ids:
                    if self.bullet_owner.get(bullet_id) != entry_id:
                        raise ValueError(f"Track '{name}': bullet '{bullet_id}' is not part of '{entry_id}'")
            vocab = {v.lower() for v in self.claimable_vocabulary()}
            for names in track.get("skills", {}).values():
                for skill in names:
                    if skill.lower() not in vocab:
                        raise ValueError(f"Track '{name}' lists skill '{skill}' that is not claimable in the profile")
        levels = {s["level"] for s in self.data["skills"]}
        unknown = levels - CLAIMABLE - {"academic"}
        if unknown:
            raise ValueError(f"Unknown skill levels: {unknown}")

    # ---- convenience views -------------------------------------------------
    @property
    def contact(self) -> dict[str, str]:
        return self.data["contact"]

    @property
    def preferences(self) -> dict[str, Any]:
        return self.data["preferences"]

    @property
    def tracks(self) -> dict[str, Any]:
        return self.data["tracks"]

    def skills(self, levels: set[str] | None = None) -> list[dict[str, str]]:
        return [s for s in self.data["skills"] if levels is None or s["level"] in levels]

    def skill_level(self, name: str) -> str | None:
        lowered = name.strip().lower()
        for skill in self.data["skills"]:
            if skill["name"].lower() == lowered:
                return skill["level"]
        return None

    def claimable_vocabulary(self) -> set[str]:
        """Every skill/tool name that may appear on a resume outside 'Familiar with'."""
        vocab = {s["name"] for s in self.skills(CLAIMABLE)}
        for entry in self.entries.values():
            vocab.update(entry.get("stack", []))
        return vocab

    def bullet_text(self, bullet_id: str, track: str | None = None) -> str:
        bullet = self.bullets[bullet_id]
        if track and track in (bullet.get("variants") or {}):
            return bullet["variants"][track]
        return bullet["text"]

    def all_bullet_texts(self, bullet_id: str) -> list[str]:
        bullet = self.bullets[bullet_id]
        return [bullet["text"], *(bullet.get("variants") or {}).values()]
