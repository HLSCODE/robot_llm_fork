"""Resolve user-owned application data independently from built-in defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import DataSettings
from .robot_profile import normalize_robot_profile_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class ApplicationDataPaths:
    root: Path
    robot_profile_id: str
    profile_root: Path
    actions_directory: Path
    workflows_directory: Path
    workflow_drafts_directory: Path
    skills_directory: Path
    trajectories_directory: Path

    @classmethod
    def from_settings(
        cls,
        settings: DataSettings,
        robot_profile_id: str = "unscoped",
        *,
        project_root: Path | None = None,
    ) -> ApplicationDataPaths:
        base = (project_root or PROJECT_ROOT).resolve()
        root = _resolve_path(
            settings.robot_data_dir,
            base=base,
        )
        profile_id = normalize_robot_profile_id(robot_profile_id)
        profile_root = root / "profiles" / profile_id
        return cls(
            root=root,
            robot_profile_id=profile_id,
            profile_root=profile_root,
            actions_directory=_resolve_override(
                settings.actions_library_directory,
                default=profile_root / "actions",
                base=base,
            ),
            workflows_directory=_resolve_override(
                settings.workflows_directory,
                default=profile_root / "workflows",
                base=base,
            ),
            workflow_drafts_directory=_resolve_override(
                settings.workflow_drafts_directory,
                default=profile_root / "drafts",
                base=base,
            ),
            skills_directory=_resolve_override(
                settings.skill_library_directory,
                default=root / "skills",
                base=base,
            ),
            trajectories_directory=_resolve_override(
                settings.trajectories_directory,
                default=profile_root / "trajectories",
                base=base,
            ),
        )


def _resolve_override(value: object, *, default: Path, base: Path) -> Path:
    normalized = str(value or "").strip()
    if not normalized:
        return default.resolve()
    return _resolve_path(normalized, base=base)


def _resolve_path(value: str, *, base: Path) -> Path:
    normalized = value.strip()
    if not normalized:
        raise ValueError("application data path must not be empty")
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()
