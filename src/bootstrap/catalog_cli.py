"""Validate active catalogs or migrate legacy files to profiled catalogs."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Sequence

from ..application.action_catalog_normalization import normalize_legacy_action
from ..persistence.json_documents import (
    CollectionDocumentSpec,
    JsonDocumentSchemaError,
    load_collection_document,
    load_single_document,
    read_json_document,
    write_collection_document,
    write_json_atomic,
    write_single_document,
)
from ..persistence.storage import ACTION_LIBRARY_DOCUMENT, JsonCompositionRepository
from ..domain.models import ActionDefinition
from ..domain.models import ActionType
from ..geometry.pose_compensation import parse_pose
from ..skill_system.models import Skill
from ..skill_system.skill_registry import SKILL_DOCUMENT, SkillRegistry


_LEGACY_ACTIONS = CollectionDocumentSpec(
    schema="robot_llm.actions",
    collection_key="actions",
    legacy_kind="list",
)
_LEGACY_SKILLS = CollectionDocumentSpec(
    schema="robot_llm.skills",
    collection_key="skills",
    legacy_kind="mapping",
)
_SAFE_FILE_STEM = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True, slots=True)
class CatalogReport:
    action_count: int
    skill_count: int
    action_fingerprint: str
    skill_fingerprint: str
    written_files: tuple[str, ...] = ()


def validate_catalogs(
    actions_directory: Path,
    skills_directory: Path,
    *,
    robot_profile_id: str = "unscoped",
) -> CatalogReport:
    repository = JsonCompositionRepository(
        robot_profile_id=robot_profile_id,
        actions_directory=actions_directory,
        workflows_directory=actions_directory.parent / "workflows",
        workflow_drafts_directory=actions_directory.parent / "drafts",
    )
    actions = repository.load_actions()
    registry = SkillRegistry()
    registry.load_directory(skills_directory)
    skills = registry.list_skills()
    return _report(actions, skills)


def migrate_catalogs(
    *,
    legacy_actions_file: Path,
    legacy_skills_file: Path,
    actions_directory: Path,
    skills_directory: Path,
    archive_directory: Path | None = None,
    robot_profile_id: str = "unscoped",
) -> CatalogReport:
    """Stage and validate all documents before publishing any target file."""
    actions = [
        _with_robot_profile(action, robot_profile_id)
        for action in _load_legacy_actions(legacy_actions_file)
    ]
    skills = _load_legacy_skills(legacy_skills_file)

    staging_parent = actions_directory.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".catalog-migration-", dir=staging_parent) as name:
        staging_root = Path(name)
        staged_actions = staging_root / "actions"
        staged_skills = staging_root / "skills"
        _write_catalogs(
            staged_actions,
            staged_skills,
            actions,
            skills,
            robot_profile_id=robot_profile_id,
        )
        staged_report = validate_catalogs(
            staged_actions,
            staged_skills,
            robot_profile_id=robot_profile_id,
        )
        expected_report = _report(actions, skills)
        if staged_report != expected_report:
            raise RuntimeError("staged catalog semantic fingerprint mismatch")

        written = _publish_catalogs(
            staged_actions,
            staged_skills,
            actions_directory,
            skills_directory,
        )
    active_report = validate_catalogs(
        actions_directory,
        skills_directory,
        robot_profile_id=robot_profile_id,
    )
    if active_report != expected_report:
        raise RuntimeError("published catalog semantic fingerprint mismatch")
    if archive_directory is not None:
        written += _archive_legacy_files(
            (legacy_actions_file, legacy_skills_file),
            archive_directory,
        )
    return CatalogReport(
        action_count=active_report.action_count,
        skill_count=active_report.skill_count,
        action_fingerprint=active_report.action_fingerprint,
        skill_fingerprint=active_report.skill_fingerprint,
        written_files=written,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or migrate robot-llm Action/Skill catalogs.",
    )
    parser.add_argument("operation", choices=("validate", "migrate"))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--actions-directory", type=Path)
    parser.add_argument("--skills-directory", type=Path)
    parser.add_argument("--legacy-actions-file", type=Path)
    parser.add_argument("--legacy-skills-file", type=Path)
    parser.add_argument("--robot-profile", default="unscoped")
    parser.add_argument(
        "--archive-legacy",
        action="store_true",
        help="Move migrated v1 source files into data/migration-backups/catalog-v1.",
    )
    arguments = parser.parse_args(argv)
    root = arguments.data_root.resolve()
    actions_directory = (arguments.actions_directory or root / "actions").resolve()
    skills_directory = (arguments.skills_directory or root / "skills").resolve()
    if arguments.operation == "validate":
        report = validate_catalogs(
            actions_directory,
            skills_directory,
            robot_profile_id=arguments.robot_profile,
        )
    else:
        report = migrate_catalogs(
            legacy_actions_file=(
                arguments.legacy_actions_file or root / "actions_library.json"
            ).resolve(),
            legacy_skills_file=(
                arguments.legacy_skills_file
                or root / "skills" / "skill_library.json"
            ).resolve(),
            actions_directory=actions_directory,
            skills_directory=skills_directory,
            archive_directory=(
                root / "migration-backups" / "catalog-v1"
                if arguments.archive_legacy
                else None
            ),
            robot_profile_id=arguments.robot_profile,
        )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


def _load_legacy_actions(path: Path) -> list[ActionDefinition]:
    document = load_collection_document(path, _LEGACY_ACTIONS)
    actions: list[ActionDefinition] = []
    ids: set[str] = set()
    names: set[str] = set()
    for index, raw_action in enumerate(document.collection):
        if not isinstance(raw_action, dict):
            raise JsonDocumentSchemaError(f"{path.name} action {index} is not an object")
        try:
            action = ActionDefinition.from_dict(raw_action)
        except (KeyError, TypeError, ValueError) as exc:
            raise JsonDocumentSchemaError(f"{path.name} action {index} is invalid") from exc
        if not action.id or action.id in ids or action.name in names:
            raise JsonDocumentSchemaError(
                f"{path.name} action {index} has a missing or duplicate id/name"
            )
        ids.add(action.id)
        names.add(action.name)
        actions.append(normalize_legacy_action(action))
    return actions


def _load_legacy_skills(path: Path) -> list[Skill]:
    document = load_collection_document(path, _LEGACY_SKILLS)
    normalized = deepcopy(document.collection)
    skills: list[Skill] = []
    ids: set[str] = set()
    for index, raw_skill in enumerate(normalized):
        if not isinstance(raw_skill, dict):
            raise JsonDocumentSchemaError(f"{path.name} skill {index} is not an object")
        for parameter in raw_skill.get("parameters", []):
            if isinstance(parameter, dict):
                parameter.setdefault("unit", "")
        for step in raw_skill.get("steps", []):
            if isinstance(step, dict):
                step.setdefault("parameter_bindings", {})
        try:
            skill = Skill.from_dict(raw_skill)
        except (KeyError, TypeError, ValueError) as exc:
            raise JsonDocumentSchemaError(f"{path.name} skill {index} is invalid") from exc
        if not _SAFE_FILE_STEM.fullmatch(skill.id) or skill.id in ids:
            raise JsonDocumentSchemaError(
                f"{path.name} skill {index} has an unsafe or duplicate id"
            )
        ids.add(skill.id)
        skills.append(_normalize_skill(skill))
    return skills


def normalize_action_catalog(actions_directory: Path) -> int:
    """Rewrite legacy textual robot poses in an active profiled catalog."""
    path = actions_directory / "library.json"
    document = load_collection_document(path, ACTION_LIBRARY_DOCUMENT)
    if document.requires_migration:
        raise JsonDocumentSchemaError(f"{path.name} is not an action schema v3 document")
    actions = [
        ActionDefinition.from_dict(raw_action)
        for raw_action in document.collection
        if isinstance(raw_action, dict)
    ]
    if len(actions) != len(document.collection):
        raise JsonDocumentSchemaError(f"{path.name} contains a non-object action")
    normalized = [normalize_legacy_action(action) for action in actions]
    changed = sum(
        before.parameters != after.parameters
        for before, after in zip(actions, normalized, strict=True)
    )
    if changed:
        write_collection_document(
            path,
            ACTION_LIBRARY_DOCUMENT,
            [action.to_dict() for action in normalized],
            metadata=document.metadata,
        )
    return changed


def _with_robot_profile(
    action: ActionDefinition,
    robot_profile_id: str,
) -> ActionDefinition:
    normalized = action.to_dict()
    normalized["robot_profile_id"] = robot_profile_id
    return ActionDefinition.from_dict(normalized)


def normalize_skill_catalog(skills_directory: Path) -> int:
    """Rewrite textual poses stored inside v2 SkillStep snapshots."""
    changed = 0
    for path in sorted(skills_directory.rglob("*.skill.json")):
        document = read_json_document(path)
        raw_skill = load_single_document(path, SKILL_DOCUMENT)
        skill = Skill.from_dict(raw_skill)
        normalized = _normalize_skill(skill)
        has_current_reference = (
            isinstance(document, dict)
            and document.get("$schema") == SKILL_DOCUMENT.schema_reference
        )
        if skill.to_dict() == normalized.to_dict() and has_current_reference:
            continue
        write_single_document(path, SKILL_DOCUMENT, normalized.to_dict())
        changed += 1
    return changed


def _normalize_skill(skill: Skill) -> Skill:
    normalized = deepcopy(skill)
    for step in normalized.steps:
        action_type = step.action_type.strip().upper()
        if action_type not in {ActionType.MOVE.name, ActionType.MOVE.value}:
            continue
        if "点位" in step.parameters:
            step.parameters["点位"] = parse_pose(step.parameters["点位"])
    return normalized


def _write_catalogs(
    actions_directory: Path,
    skills_directory: Path,
    actions: Sequence[ActionDefinition],
    skills: Sequence[Skill],
    *,
    robot_profile_id: str,
) -> None:
    write_collection_document(
        actions_directory / "library.json",
        ACTION_LIBRARY_DOCUMENT,
        [action.to_dict() for action in actions],
        metadata={"robot_profile_id": robot_profile_id},
    )
    for skill in skills:
        path = skills_directory / skill.category.name.lower() / f"{skill.id}.skill.json"
        write_single_document(path, SKILL_DOCUMENT, skill.to_dict())


def _publish_catalogs(
    staged_actions: Path,
    staged_skills: Path,
    actions_directory: Path,
    skills_directory: Path,
) -> tuple[str, ...]:
    if (actions_directory / "library.json").exists():
        raise FileExistsError(actions_directory / "library.json")
    if any(skills_directory.rglob("*.skill.json")):
        raise FileExistsError("target skill directory already contains v2 skill files")
    written: list[str] = []
    for source, destination in (
        (staged_actions / "library.json", actions_directory / "library.json"),
        *((path, skills_directory / path.relative_to(staged_skills))
          for path in sorted(staged_skills.rglob("*.skill.json"))),
    ):
        write_json_atomic(destination, json.loads(source.read_text(encoding="utf-8")))
        written.append(destination.as_posix())
    return tuple(written)


def _archive_legacy_files(
    sources: Sequence[Path],
    archive_directory: Path,
) -> tuple[str, ...]:
    archived: list[str] = []
    archive_directory.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = archive_directory / source.name
        if destination.exists():
            raise FileExistsError(destination)
        source.replace(destination)
        archived.append(destination.as_posix())
    return tuple(archived)


def _report(
    actions: Sequence[ActionDefinition],
    skills: Sequence[Skill],
) -> CatalogReport:
    return CatalogReport(
        action_count=len(actions),
        skill_count=len(skills),
        action_fingerprint=_fingerprint(
            [action.to_dict() for action in sorted(actions, key=lambda item: item.id)]
        ),
        skill_fingerprint=_fingerprint(
            [skill.to_dict() for skill in sorted(skills, key=lambda item: item.id)]
        ),
    )


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
