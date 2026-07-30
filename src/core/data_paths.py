"""Resolve user-owned application data independently from built-in defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class ApplicationDataPaths:
    root: Path
    actions_file: Path
    tasks_directory: Path
    skills_file: Path

    @classmethod
    def from_config(cls, config: Any) -> ApplicationDataPaths:
        root = _resolve_path(
            str(getattr(config, "ROBOT_DATA_DIR", "data")),
            base=PROJECT_ROOT,
        )
        return cls(
            root=root,
            actions_file=_resolve_override(
                getattr(config, "ACTIONS_LIBRARY_PATH", ""),
                default=root / "actions_library.json",
            ),
            tasks_directory=_resolve_override(
                getattr(config, "TASKS_DIRECTORY", ""),
                default=root / "tasks",
            ),
            skills_file=_resolve_override(
                getattr(config, "SKILL_LIBRARY_PATH", ""),
                default=root / "skills" / "skill_library.json",
            ),
        )


def _resolve_override(value: object, *, default: Path) -> Path:
    normalized = str(value or "").strip()
    if not normalized:
        return default.resolve()
    return _resolve_path(normalized, base=PROJECT_ROOT)


def _resolve_path(value: str, *, base: Path) -> Path:
    normalized = value.strip()
    if not normalized:
        raise ValueError("application data path must not be empty")
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()
