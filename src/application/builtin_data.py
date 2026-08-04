"""Install immutable built-in catalogs into an empty user data directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..configuration.data_paths import ApplicationDataPaths
from ..persistence.json_documents import write_collection_document
from ..domain.models import ActionDefinition, ActionType
from ..persistence.storage import ACTION_LIBRARY_DOCUMENT
from ..skill_system.default_skills import get_default_skills
from ..skill_system.skill_registry import SKILL_LIBRARY_DOCUMENT


@dataclass(frozen=True, slots=True)
class BuiltinDataInstallResult:
    created_files: tuple[Path, ...]


class BuiltinDataInstaller:
    """Seed missing user catalogs once without overwriting user changes."""

    def __init__(self, paths: ApplicationDataPaths) -> None:
        self._paths = paths

    def install_missing(self) -> BuiltinDataInstallResult:
        created_files: list[Path] = []
        self._paths.tasks_directory.mkdir(parents=True, exist_ok=True)

        if not self._paths.actions_file.exists():
            write_collection_document(
                self._paths.actions_file,
                ACTION_LIBRARY_DOCUMENT,
                [action.to_dict() for action in _builtin_actions()],
            )
            created_files.append(self._paths.actions_file)

        if not self._paths.skills_file.exists():
            write_collection_document(
                self._paths.skills_file,
                SKILL_LIBRARY_DOCUMENT,
                [skill.to_dict() for skill in get_default_skills()],
            )
            created_files.append(self._paths.skills_file)

        return BuiltinDataInstallResult(tuple(created_files))


def _builtin_actions() -> tuple[ActionDefinition, ...]:
    return (
        ActionDefinition(
            id="builtin.wait.1s",
            name="等待 1 秒",
            type=ActionType.WAIT,
            parameters={"wait_seconds": 1.0},
        ),
        ActionDefinition(
            id="builtin.wait.3s",
            name="等待 3 秒",
            type=ActionType.WAIT,
            parameters={"wait_seconds": 3.0},
        ),
    )
