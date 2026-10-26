"""Explicit, staged data upgrades. Never discover or connect hardware here."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .action_catalog_normalization import normalize_legacy_action
from ..configuration.data_paths import ApplicationDataPaths
from ..domain.action_schema import (
    validate_action_parameters,
    PENDING_TARGET_FIELDS,
    pending_target_fields,
)
from ..domain.models import ActionDefinition, ActionType
from ..domain.workflow import WorkflowDocument, WORKFLOW_DOCUMENT_VERSION
from ..persistence.json_documents import read_json_document, write_json_atomic
from ..persistence.storage import ACTION_LIBRARY_DOCUMENT, JsonCompositionRepository
from ..skill_system.skill_registry import SKILL_DOCUMENT, load_skill_documents


@dataclass(frozen=True)
class TargetMappings:
    """Explicit user choices: action IDs take precedence over executor defaults."""

    actions: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    defaults: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None) -> TargetMappings:
        if path is None:
            return cls()
        raw = read_json_document(path)
        if not isinstance(raw, dict) or set(raw) - {"actions", "defaults"}:
            raise ValueError("迁移映射须为包含 actions/defaults 的 JSON 对象")
        for section in raw.values():
            if not isinstance(section, dict):
                raise ValueError("迁移映射分组须为对象")
            for target in section.values():
                if not isinstance(target, dict) or set(target) - {"arm", "tool_id"}:
                    raise ValueError("目标映射仅允许 arm、tool_id")
                if target.get("arm") not in ("left", "right"):
                    raise ValueError("每个目标映射必须明确 arm=left/right")
                if "tool_id" in target and (
                    not isinstance(target["tool_id"], str) or not target["tool_id"].strip()
                ):
                    raise ValueError("tool_id 必须为非空字符串")
        return cls(raw.get("actions", {}), raw.get("defaults", {}))


def upgrade_versions(
    document: dict[str, Any],
    current: int,
    steps: Mapping[int, Callable[[dict[str, Any]], None]],
) -> tuple[dict[str, Any], tuple[int, ...]]:
    """Apply every registered adjacent upgrade to a private copy, or fail."""
    result = deepcopy(document)
    version = result.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or not 0 <= version <= current:
        raise ValueError(f"不支持的数据版本 {version!r}，当前版本为 {current}")
    versions = [version]
    while version < current:
        if version not in steps:
            raise ValueError(f"缺少 v{version} → v{version + 1} 迁移器")
        steps[version](result)
        version += 1
        result["schema_version"] = version
        versions.append(version)
    return result, tuple(versions)


@dataclass(frozen=True)
class MigrationChange:
    path: Path
    original: bytes | None
    document: dict[str, Any] | None
    versions: tuple[int, ...]
    content: bytes | None = None

    @property
    def pending_actions(self) -> tuple[str, ...]:
        names: list[str] = []

        def collect(value: object) -> None:
            if isinstance(value, dict):
                if pending_target_fields(value.get("parameters")):
                    names.append(
                        str(
                            value.get("name")
                            or value.get("action_name")
                            or value.get("id")
                            or "未命名动作"
                        )
                    )
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(self.document)
        return tuple(names)


@dataclass(frozen=True)
class DataMigrationPlan:
    changes: tuple[MigrationChange, ...]
    issues: tuple[str, ...]

    def apply(self, backup_root: Path) -> Path | None:
        if self.issues:
            raise ValueError("迁移尚有未解决问题，未写入任何数据：\n" + "\n".join(self.issues))
        if not self.changes:
            return None
        for change in self.changes:
            current = change.path.read_bytes() if change.path.exists() else None
            if current != change.original:
                raise ValueError(f"预览后文件发生变化，请重新规划迁移：{change.path}")
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="upgrade-", dir=backup_root))
        manifest = []
        for index, change in enumerate(self.changes):
            copy = backup / f"{index:04d}.json"
            if change.original is not None:
                shutil.copy2(change.path, copy)
            manifest.append(
                {
                    "path": str(change.path),
                    "backup": copy.name if change.original is not None else None,
                }
            )
        write_json_atomic(backup / "manifest.json", {"files": manifest})
        published: list[MigrationChange] = []
        try:
            for change in self.changes:
                published.append(change)
                if change.document is not None:
                    write_json_atomic(change.path, change.document)
                elif change.content is not None:
                    _write_bytes_atomic(change.path, change.content)
                else:
                    raise ValueError(f"迁移内容为空：{change.path}")
        except BaseException:
            for change in reversed(published):
                try:
                    if change.original is None:
                        change.path.unlink(missing_ok=True)
                    else:
                        _write_bytes_atomic(change.path, change.original)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "迁移回滚失败，请从备份恢复：%s", change.path
                    )
            raise
        return backup


class DataMigrator:
    def __init__(
        self,
        paths: ApplicationDataPaths,
        mappings: TargetMappings | None = None,
        legacy_workflow_reader: Callable[[Path], dict[str, Any]] | None = None,
    ) -> None:
        self.paths = paths
        self.mappings = mappings or TargetMappings()
        self.legacy_workflow_reader = legacy_workflow_reader

    def plan(self) -> DataMigrationPlan:
        changes: list[MigrationChange] = []
        issues: list[str] = []
        paths = {self.paths.actions_directory / "library.json"}
        for directory, pattern in (
            (self.paths.workflows_directory, "*.workflow.json"),
            (self.paths.workflow_drafts_directory, "*.workflow.json"),
            (self.paths.skills_directory, "*.skill.json"),
        ):
            paths.update(directory.rglob(pattern))
        # Legacy unprofiled libraries may only be explicitly moved into RealMan.
        target = self.paths.actions_directory / "library.json"
        if not target.exists() and self.paths.robot_profile_id == "realman-rm75-dual":
            source = next(
                (
                    p
                    for p in (
                        self.paths.root / "actions/library.json",
                        self.paths.root / "actions_library.json",
                    )
                    if p.is_file()
                ),
                None,
            )
            if source is not None:
                paths.discard(target)
                self._plan_file(source, target, changes, issues)
        if self.paths.robot_profile_id == "realman-rm75-dual":
            for source_dir, target_dir in (
                (self.paths.root / "workflows", self.paths.workflows_directory),
                (self.paths.root / "drafts", self.paths.workflow_drafts_directory),
            ):
                for source in sorted(source_dir.glob("*.workflow.json")):
                    target = target_dir / source.name
                    if source != target and not target.exists():
                        self._plan_file(source, target, changes, issues)
            legacy_trajectories = self.paths.root / "trajectories"
            if legacy_trajectories != self.paths.trajectories_directory:
                for source in sorted(legacy_trajectories.rglob("*")):
                    target = self.paths.trajectories_directory / source.relative_to(
                        legacy_trajectories
                    )
                    if source.is_file() and not target.exists():
                        try:
                            changes.append(
                                MigrationChange(target, None, None, (), source.read_bytes())
                            )
                        except OSError as error:
                            issues.append(f"{source}: {error}")
        for path in sorted(paths):
            if path.is_file():
                self._plan_file(path, path, changes, issues)
        if self.legacy_workflow_reader is not None:
            for source in sorted((self.paths.root / "tasks").glob("*")):
                if source.suffix not in (".task", ".workflow") and source.name != ".workflow-draft":
                    continue
                target = (
                    self.paths.workflow_drafts_directory / "current.draft.workflow.json"
                    if source.name == ".workflow-draft"
                    else self.paths.workflows_directory / f"{source.stem}.workflow.json"
                )
                if target.exists():
                    continue  # Existing canonical documents are never overwritten.
                self._plan_file(source, target, changes, issues)
        targets = [change.path for change in changes]
        if len(targets) != len(set(targets)):
            issues.append("多个旧文件对应同一迁移目标，请先消除命名冲突")
        if not issues:
            skills = sorted(self.paths.skills_directory.rglob("*.skill.json"))
            converted = {change.path: change.document for change in changes}
            try:
                with tempfile.TemporaryDirectory(prefix="robot-skills-validation-") as skill_stage:
                    for index, path in enumerate(skills):
                        write_json_atomic(
                            Path(skill_stage) / f"{index}.skill.json",
                            converted.get(path) or read_json_document(path),
                        )
                    load_skill_documents(Path(skill_stage))
            except (OSError, ValueError, TypeError, KeyError) as error:
                issues.append(f"技能目录校验失败：{error}")
        return DataMigrationPlan(tuple(changes), tuple(issues))

    def _plan_file(
        self, source: Path, target: Path, changes: list[MigrationChange], issues: list[str]
    ) -> None:
        try:
            original = source.read_bytes()
            raw = json.loads(original)
            self._require_profiles(raw)
            if source.suffix in (".task", ".workflow") or source.name == ".workflow-draft":
                if self.legacy_workflow_reader is None:
                    raise ValueError("旧任务格式需要通过 robot-init migrate-data 迁移")
                raw = self.legacy_workflow_reader(source)
                raw["schema_version"] = 5
            if isinstance(raw, list):
                raw = {"schema": "robot_llm.actions", "schema_version": 0, "actions": raw}
            if not isinstance(raw, dict):
                raise ValueError("数据必须为 JSON 对象或旧动作列表")
            schema = raw.get("schema")
            if schema == "robot_llm.actions":
                if source != target and self.paths.robot_profile_id == "realman-rm75-dual":
                    if raw.get("robot_profile_id", "unscoped") == "unscoped":
                        raw["robot_profile_id"] = self.paths.robot_profile_id
                    self._profile(raw)
                result, versions = upgrade_versions(
                    raw,
                    ACTION_LIBRARY_DOCUMENT.current_version,
                    {
                        0: self._legacy_actions,
                        1: lambda d: self._actions(d, normalize=True),
                        2: self._profile,
                        3: self._actions,
                    },
                )
                if len(versions) > 1:
                    result.setdefault("$schema", ACTION_LIBRARY_DOCUMENT.schema_reference)
                self._validate_library(result)
            elif schema == "robot_llm.workflow":
                if isinstance(raw.get("schema_version"), bool):
                    raise ValueError("工作流版本不能为布尔值")
                if raw.get("schema_version") == 1 and self.legacy_workflow_reader is not None:
                    raw = self.legacy_workflow_reader(source)
                    raw["schema_version"] = 5
                if source != target and self.paths.robot_profile_id == "realman-rm75-dual":
                    raw.setdefault("robot_profile_id", self.paths.robot_profile_id)
                result, versions = upgrade_versions(
                    raw, WORKFLOW_DOCUMENT_VERSION, {5: self._workflow}
                )
                WorkflowDocument.from_dict(result)
                self._walk_definitions(result, migrate=False)
            elif schema == "robot_llm.skill":
                result, versions = upgrade_versions(
                    raw, SKILL_DOCUMENT.current_version, {1: self._skill_defaults, 2: self._skill}
                )
                with tempfile.TemporaryDirectory(prefix="robot-skill-validation-") as directory:
                    staged = Path(directory) / "check.skill.json"
                    write_json_atomic(staged, result)
                    load_skill_documents(Path(directory))
            else:
                raise ValueError(f"未知 schema {schema!r}")
            if result != json.loads(original) or source != target:
                changes.append(
                    MigrationChange(
                        target, target.read_bytes() if target.exists() else None, result, versions
                    )
                )
        except (OSError, ValueError, TypeError, KeyError) as error:
            issues.append(f"{source}: {error}")

    def _profile(self, document: dict[str, Any]) -> None:
        declared = document.get("robot_profile_id", self.paths.robot_profile_id)
        if declared != self.paths.robot_profile_id:
            raise ValueError(f"Robot Profile 不匹配：{declared}")
        document["robot_profile_id"] = declared
        for action in document["actions"]:
            if action.get("robot_profile_id", "unscoped") not in ("unscoped", declared):
                raise ValueError("旧动作包含其他 Robot Profile，禁止跨机器人迁移")
            action["robot_profile_id"] = declared

    def _require_profiles(self, value: object) -> None:
        if isinstance(value, dict):
            if value.get("robot_profile_id", "unscoped") not in (
                "unscoped",
                self.paths.robot_profile_id,
            ):
                raise ValueError("Robot Profile 不匹配，禁止跨机器人迁移")
            for child in value.values():
                self._require_profiles(child)
        elif isinstance(value, list):
            for child in value:
                self._require_profiles(child)

    @staticmethod
    def _legacy_actions(document: dict[str, Any]) -> None:
        for index, action in enumerate(document["actions"]):
            action.setdefault("id", f"legacy-{index}")
            action["type"] = _action_type(action["type"]).value

    def _actions(self, document: dict[str, Any], *, normalize: bool = False) -> None:
        for action in document["actions"]:
            if normalize:
                action.update(normalize_legacy_action(ActionDefinition.from_dict(action)).to_dict())
            else:
                self._action(action)

    def _workflow(self, document: dict[str, Any]) -> None:
        if document.get("robot_profile_id") != self.paths.robot_profile_id:
            raise ValueError("工作流 Robot Profile 不匹配，禁止跨机器人迁移")
        self._walk_definitions(document, migrate=True)

    def _walk_definitions(self, value: object, *, migrate: bool) -> None:
        if isinstance(value, dict):
            if value.get("kind") == "action":
                action = value["definition"]
                if action.get("robot_profile_id", "unscoped") not in (
                    "unscoped",
                    self.paths.robot_profile_id,
                ):
                    raise ValueError("工作流动作 Robot Profile 不匹配")
                if migrate:
                    action["robot_profile_id"] = self.paths.robot_profile_id
                    self._action(action)
                else:
                    self._validate_action(action)
            for child in value.values():
                self._walk_definitions(child, migrate=migrate)
        elif isinstance(value, list):
            for child in value:
                self._walk_definitions(child, migrate=migrate)

    @staticmethod
    def _skill_defaults(document: dict[str, Any]) -> None:
        for parameter in document["skill"].get("parameters", []):
            parameter.setdefault("unit", "")
        for step in document["skill"]["steps"]:
            step.setdefault("parameter_bindings", {})

    def _skill(self, document: dict[str, Any]) -> None:
        for step in document["skill"]["steps"]:
            action = {
                "id": f"{document['skill']['id']}/{step['step_id']}",
                "name": step["action_name"],
                "type": _action_type(step["action_type"]).value,
                "parameters": step["parameters"],
            }
            self._action(action)
            step["parameters"] = action["parameters"]

    def _action(self, action: dict[str, Any]) -> None:
        normalized = normalize_legacy_action(ActionDefinition.from_dict(action))
        parameters = normalized.parameters
        executor = parameters.get("执行器")
        if executor == "知行夹爪":
            executor = parameters["执行器"] = "夹爪"
        if normalized.type is ActionType.MANIPULATE and executor in (
            "夹爪",
            "吸液枪",
            "快换手",
            "右臂转圈注液",
        ):
            explicit = self.mappings.actions.get(
                action.get("id", ""), self.mappings.actions.get(action["name"], {})
            )
            mapping = {**self.mappings.defaults.get(str(executor), {}), **explicit}
            for key in ("arm", "tool_id"):
                if parameters.get(key) in (None, "") and key in mapping:
                    parameters[key] = mapping[key]
            if "编号" in parameters:
                del parameters["编号"]
            pending = []
            for key in ("arm", "tool_id"):
                if parameters.get(key) in (None, ""):
                    parameters[key] = ""
                    pending.append(key)
            if pending:
                parameters[PENDING_TARGET_FIELDS] = pending
            else:
                parameters.pop(PENDING_TARGET_FIELDS, None)
        action["parameters"] = parameters
        self._validate_action(action)

    @staticmethod
    def _validate_action(action: dict[str, Any]) -> None:
        validation = validate_action_parameters(
            _action_type(action["type"]),
            action["parameters"],
            allow_pending_targets=True,
        )
        if not validation.is_valid:
            raise ValueError(f"动作 {action.get('name')!r}: {validation.message}")

    def _validate_library(self, document: dict[str, Any]) -> None:
        # Reuse the normal repository validator without publishing to user data.
        with tempfile.TemporaryDirectory(prefix="robot-action-validation-") as directory:
            root = Path(directory)
            write_json_atomic(root / "library.json", document)
            JsonCompositionRepository(
                actions_directory=root, robot_profile_id=self.paths.robot_profile_id
            ).load_actions()


def _action_type(value: str) -> ActionType:
    return ActionType.__members__.get(value) or ActionType(value)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            staged = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
