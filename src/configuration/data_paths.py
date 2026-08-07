"""Resolve user-owned application data independently from built-in defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import DataSettings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class ApplicationDataPaths:
    root: Path
    actions_directory: Path
    tasks_directory: Path
    skills_directory: Path

    @classmethod
    def from_settings(cls, settings: DataSettings) -> ApplicationDataPaths:
        root = _resolve_path(
            settings.robot_data_dir,
            base=PROJECT_ROOT,
        )
        return cls(
            root=root,
            actions_directory=_resolve_override(
                settings.actions_library_directory,
                default=root / "actions",
            ),
            tasks_directory=_resolve_override(
                settings.tasks_directory,
                default=root / "tasks",
            ),
            skills_directory=_resolve_override(
                settings.skill_library_directory,
                default=root / "skills",
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
