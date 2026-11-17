"""Tests run against the committed example profile and config, never your own files.

That way they pass the same on every machine, and never read or depend on your personal details.
"""

import os
import shutil
from pathlib import Path

# Hide the personal files in this checkout (see career/profile.py). Set before any test imports
# career.*, and inherited by the subprocesses the dashboard tests start.
os.environ["CAREER_AGENT_EXAMPLES"] = str(Path(__file__).resolve().parent.parent)

PERSONAL = ("baseline.yaml", "answers.yaml", "sources.yaml", ".env", "data", "output", "learning", "career-agent")
JUNK = ("__pycache__", ".venv", ".git", ".pytest_cache", ".ruff_cache")


def project_ignore(*extra: str):
    """For shutil.copytree when a test copies the project: leaves your personal files (and caches) behind."""
    return shutil.ignore_patterns(*PERSONAL, *JUNK, *extra)
