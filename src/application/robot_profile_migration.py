"""One-way migration of legacy shared data into a Robot Profile namespace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from .action_catalog_normalization import normalize_legacy_action
from ..configuration.data_paths import ApplicationDataPaths
from ..domain.models import ActionDefinition
from ..persistence.json_documents import (
    load_collection_document,
    read_json_document,
    write_collection_document,
    write_json_atomic,
)
from ..persistence.storage import ACTION_LIBRARY_DOCUMENT, ACTION_LIBRARY_FILE_NAME


_LEGACY_REALMAN_PROFILE = "realman-rm75-dual"


@dataclass(frozen=True, slots=True)
class RobotProfileMigrationResult:
    migrated_files: tuple[Path, ...]


class LegacyRobotProfileMigrator:
    """Copy legacy RealMan data once without mutating its original files."""

    def __init__(self, paths: ApplicationDataPaths, *, provider: str) -> None:
        self._paths = paths
        self._provider = provider.strip().lower()

    def migrate_missing(self) -> RobotProfileMigrationResult:
        if (
            self._provider != "realman"
            or self._paths.robot_profile_id != _LEGACY_REALMAN_PROFILE
        ):
            return RobotProfileMigrationResult(())

        migrated: list[Path] = []
        action_target = self._paths.actions_directory / ACTION_LIBRARY_FILE_NAME
        if not action_target.exists():
            source = self._legacy_action_source()
            if source is not None:
                self._migrate_actions(source, action_target)
                migrated.append(action_target)
        elif self._normalize_existing_actions(action_target):
            migrated.append(action_target)

        migrated.extend(
            self._migrate_workflow_directory(
                self._paths.root / "workflows",
                self._paths.workflows_directory,
                "*.workflow.json",
            )
        )
        migrated.extend(
            self._migrate_workflow_directory(
                self._paths.root / "drafts",
                self._paths.workflow_drafts_directory,
                "*.workflow.json",
            )
        )
        migrated.extend(
            self._copy_missing_tree(
                self._paths.root / "trajectories",
                self._paths.trajectories_directory,
            )
        )
        return RobotProfileMigrationResult(tuple(migrated))

    def _legacy_action_source(self) -> Path | None:
        candidates = (
            self._paths.root / "actions" / ACTION_LIBRARY_FILE_NAME,
            self._paths.root / "actions_library.json",
        )
        return next((path for path in candidates if path.is_file()), None)

    def _migrate_actions(self, source: Path, target: Path) -> None:
        raw = read_json_document(source)
        if isinstance(raw, list):
            actions = raw
        elif isinstance(raw, dict) and isinstance(raw.get("actions"), list):
            actions = raw["actions"]
        else:
            raise ValueError(f"{source.name}: legacy action library is invalid")
        profiled_actions: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ValueError(f"{source.name}: action {index} must be an object")
            profiled = dict(action)
            profiled["robot_profile_id"] = self._paths.robot_profile_id
            profiled_actions.append(
                normalize_legacy_action(ActionDefinition.from_dict(profiled)).to_dict()
            )
        write_collection_document(
            target,
            ACTION_LIBRARY_DOCUMENT,
            profiled_actions,
            metadata={"robot_profile_id": self._paths.robot_profile_id},
        )

    def _normalize_existing_actions(self, target: Path) -> bool:
        document = load_collection_document(target, ACTION_LIBRARY_DOCUMENT)
        actions: list[ActionDefinition] = []
        for index, raw_action in enumerate(document.collection):
            if not isinstance(raw_action, dict):
                raise ValueError(f"{target.name}: action {index} must be an object")
            actions.append(normalize_legacy_action(ActionDefinition.from_dict(raw_action)))

        if all(
            raw_action == action.to_dict()
            for raw_action, action in zip(document.collection, actions, strict=True)
        ):
            return False
        write_collection_document(
            target,
            ACTION_LIBRARY_DOCUMENT,
            [action.to_dict() for action in actions],
            metadata=document.metadata,
        )
        return True

    def _migrate_workflow_directory(
        self,
        source_directory: Path,
        target_directory: Path,
        pattern: str,
    ) -> list[Path]:
        if not source_directory.is_dir() or source_directory == target_directory:
            return []
        migrated: list[Path] = []
        for source in sorted(source_directory.glob(pattern)):
            if not source.is_file():
                continue
            target = target_directory / source.name
            if target.exists():
                continue
            raw = read_json_document(source)
            if not isinstance(raw, dict) or raw.get("schema") != "robot_llm.workflow":
                continue
            profiled = dict(raw)
            profiled["schema_version"] = 5
            profiled["robot_profile_id"] = self._paths.robot_profile_id
            profiled["$schema"] = "../../../schemas/workflow.schema.json"
            self._stamp_action_profiles(profiled)
            write_json_atomic(target, profiled)
            migrated.append(target)
        return migrated

    def _stamp_action_profiles(self, value: object) -> None:
        if isinstance(value, dict):
            if value.get("kind") == "action" and isinstance(
                value.get("definition"), dict
            ):
                value["definition"]["robot_profile_id"] = (
                    self._paths.robot_profile_id
                )
            for nested in value.values():
                self._stamp_action_profiles(nested)
        elif isinstance(value, list):
            for nested in value:
                self._stamp_action_profiles(nested)

    @staticmethod
    def _copy_missing_tree(source: Path, target: Path) -> list[Path]:
        if not source.is_dir() or source == target:
            return []
        copied: list[Path] = []
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            destination = target / path.relative_to(source)
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied.append(destination)
        return copied
