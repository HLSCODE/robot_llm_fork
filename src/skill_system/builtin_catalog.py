"""Read immutable built-in skill JSON resources."""

from pathlib import Path

from .models import Skill
from .skill_registry import load_skill_documents


BUILTIN_SKILLS_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "builtin_catalogs" / "skills"
)


def get_builtin_skills() -> tuple[Skill, ...]:
    return load_skill_documents(BUILTIN_SKILLS_DIRECTORY)
