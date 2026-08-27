"""Install immutable built-in catalogs into an empty user data directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..configuration.data_paths import ApplicationDataPaths
from ..persistence.json_documents import (
    read_json_document,
    write_collection_document,
    write_json_atomic,
)
from ..persistence.storage import ACTION_LIBRARY_DOCUMENT, ACTION_LIBRARY_FILE_NAME


_BUILTIN_CATALOG_ROOT = Path(__file__).resolve().parents[1] / "builtin_catalogs"


@dataclass(frozen=True, slots=True)
class BuiltinDataInstallResult:
    created_files: tuple[Path, ...]


class BuiltinDataInstaller:
    """Seed missing user catalogs once without overwriting user changes."""

    def __init__(self, paths: ApplicationDataPaths) -> None:
        self._paths = paths

    def install_missing(self) -> BuiltinDataInstallResult:
        created_files: list[Path] = []
        self._paths.workflows_directory.mkdir(parents=True, exist_ok=True)
        self._paths.workflow_drafts_directory.mkdir(parents=True, exist_ok=True)
        self._paths.trajectories_directory.mkdir(parents=True, exist_ok=True)

        for source in sorted((_BUILTIN_CATALOG_ROOT / "schemas").glob("*.json")):
            destination = self._paths.root / "schemas" / source.name
            destination_missing = not destination.exists()
            if destination_missing or _json_documents_differ(source, destination):
                _copy_json(source, destination)
            if destination_missing:
                created_files.append(destination)

        actions_file = (
            self._paths.actions_directory / ACTION_LIBRARY_FILE_NAME
        )
        if not actions_file.exists():
            source = read_json_document(
                _BUILTIN_CATALOG_ROOT / "actions" / ACTION_LIBRARY_FILE_NAME
            )
            source_profile = str(source.get("robot_profile_id", ""))
            actions = (
                source.get("actions", [])
                if self._paths.robot_profile_id == source_profile
                else []
            )
            profiled_actions = [
                {**action, "robot_profile_id": self._paths.robot_profile_id}
                for action in actions
                if isinstance(action, dict)
            ]
            write_collection_document(
                actions_file,
                ACTION_LIBRARY_DOCUMENT,
                profiled_actions,
                metadata={"robot_profile_id": self._paths.robot_profile_id},
            )
            created_files.append(actions_file)

        if not any(self._paths.skills_directory.rglob("*.skill.json")):
            source_directory = _BUILTIN_CATALOG_ROOT / "skills"
            for source in sorted(source_directory.rglob("*.skill.json")):
                destination = (
                    self._paths.skills_directory
                    / source.relative_to(source_directory)
                )
                _copy_json(source, destination)
                created_files.append(destination)

        return BuiltinDataInstallResult(tuple(created_files))


def _copy_json(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    document = read_json_document(source)
    if isinstance(document, dict):
        schema_references = {
            "robot_llm.actions": "../schemas/action-library.schema.json",
            "robot_llm.skill": "../../schemas/skill.schema.json",
        }
        schema = document.get("schema")
        reference = schema_references.get(schema) if isinstance(schema, str) else None
        if reference is not None:
            document["$schema"] = reference
    write_json_atomic(destination, document)


def _json_documents_differ(source: Path, destination: Path) -> bool:
    try:
        return read_json_document(source) != read_json_document(destination)
    except (OSError, ValueError):
        return True
