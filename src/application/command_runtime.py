"""Process-level typed command planning, approval, and execution control."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4

from ..domain.action_schema import validate_action_parameters
from ..domain.commands import (
    ActionCommand,
    ExecutionControlAction,
    ExecutionControlCommand,
    PlannedCommand,
    SkillCommand,
    WorkflowCommand,
)
from ..domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    ParallelBlock,
    SequenceEntry,
    SequenceItem,
    sequence_entry_from_dict,
)
from ..skill_system import SkillEngine
from ..skill_system.models import SkillMatchResult
from .command_catalog import CommandCatalog, CommandResolution
from .composition import CompositionService
from .workflow_compiler import WorkflowCompilationError, WorkflowCompiler


class CommandRuntimeError(RuntimeError):
    code = "command_runtime_error"


class PreviewNotFoundError(CommandRuntimeError):
    code = "preview_not_found"


class PreviewVersionConflictError(CommandRuntimeError):
    code = "preview_version_conflict"


class PreviewSourceMismatchError(CommandRuntimeError):
    code = "preview_source_mismatch"


class PreviewExpiredError(CommandRuntimeError):
    code = "preview_expired"


class PreviewStateError(CommandRuntimeError):
    code = "preview_state_error"


class RiskAcknowledgementRequiredError(CommandRuntimeError):
    code = "risk_acknowledgement_required"


class PreviewState(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommandValidationCode(str, Enum):
    VALID = "valid"
    UNSUPPORTED_COMMAND = "unsupported_command"
    ACTION_NOT_FOUND = "action_not_found"
    INVALID_ACTION_PARAMETERS = "invalid_action_parameters"
    INVALID_SKILL = "invalid_skill"
    WORKFLOW_NOT_FOUND = "workflow_not_found"
    INVALID_WORKFLOW = "invalid_workflow"
    EMPTY_SEQUENCE = "empty_sequence"


@dataclass(frozen=True, slots=True)
class CommandValidation:
    is_valid: bool
    code: CommandValidationCode
    message: str
    warnings: tuple[str, ...] = ()

    @classmethod
    def succeeded(cls, message: str) -> CommandValidation:
        return cls(True, CommandValidationCode.VALID, message)

    @classmethod
    def failed(
        cls,
        code: CommandValidationCode,
        message: str,
    ) -> CommandValidation:
        if code is CommandValidationCode.VALID:
            raise ValueError("failed command validation requires an error code")
        return cls(False, code, message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "code": self.code.value,
            "message": self.message,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    reasons: tuple[str, ...]

    @property
    def requires_acknowledgement(self) -> bool:
        return self.level is RiskLevel.HIGH

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "reasons": list(self.reasons),
            "requires_acknowledgement": self.requires_acknowledgement,
        }


@dataclass(frozen=True, slots=True)
class CommandPreview:
    preview_id: str
    version: int
    source: str
    created_at: float
    expires_at: float
    state: PreviewState
    sequence: tuple[dict[str, Any], ...]
    command_info: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]
    risk: RiskAssessment

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "version": self.version,
            "source": self.source,
            "created_at": _iso_timestamp(self.created_at),
            "expires_at": _iso_timestamp(self.expires_at),
            "state": self.state.value,
            "sequence": [_copy_dict(item) for item in self.sequence],
            "command_info": _copy_dict(self.command_info),
            "plan": _copy_dict(self.plan),
            "validation": _copy_dict(self.validation),
            "risk": self.risk.to_dict(),
            "requires_confirmation": True,
            "requires_risk_acknowledgement": self.risk.requires_acknowledgement,
        }


@dataclass(frozen=True, slots=True)
class PreviewPreparation:
    preview: CommandPreview | None
    validation: CommandValidation


@dataclass(frozen=True, slots=True)
class ConfirmedCommand:
    preview_id: str
    version: int
    source: str
    sequence: tuple[SequenceEntry, ...]


@dataclass(slots=True)
class _StoredPreview:
    preview_id: str
    version: int
    source: str
    created_at: float
    expires_at: float
    deadline: float
    state: PreviewState
    sequence: tuple[dict[str, Any], ...]
    command_info: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]
    risk: RiskAssessment


class CommandRuntime:
    """Own typed command expansion, the sole pending preview, and approval policy."""

    _HIGH_RISK_ACTIONS = frozenset(
        {
            ActionType.MOVE,
            ActionType.BASE_MOVE,
            ActionType.MANIPULATE,
            ActionType.CHANGE_GUN,
            ActionType.VISION_RELOCALIZE,
            ActionType.TRAJECTORY,
        }
    )
    _MEDIUM_RISK_ACTIONS = frozenset({ActionType.VISION_CAPTURE})

    def __init__(
        self,
        *,
        execution: Any,
        skill_engine: SkillEngine,
        composition: CompositionService,
        workflow_compiler: WorkflowCompiler,
        catalog: CommandCatalog,
        robot_profile_id: str = "unscoped",
        preview_ttl_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if preview_ttl_s <= 0:
            raise ValueError("preview_ttl_s must be positive")
        self._execution = execution
        self._skill_engine = skill_engine
        from ..domain.robot_profile import normalize_robot_profile_id

        self._composition = composition
        self._robot_profile_id = normalize_robot_profile_id(robot_profile_id)
        self._workflow_compiler = workflow_compiler
        self._catalog = catalog
        self._preview_ttl_s = float(preview_ttl_s)
        self._clock = clock
        self._wall_clock = wall_clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = RLock()
        self._version = 0
        self._current: _StoredPreview | None = None

    def command_catalog(self) -> list[dict[str, Any]]:
        return self._catalog.entries()

    def list_skills(self) -> list[dict[str, Any]]:
        """Return transport-safe skill summaries without exposing SkillEngine."""
        return self._skill_engine.list_all_skills()

    def resolve_text(self, text: str) -> CommandResolution:
        return self._catalog.resolve(text)

    def prepare(
        self,
        command: PlannedCommand,
        *,
        source: str,
        plan: dict[str, Any],
    ) -> PreviewPreparation:
        if isinstance(command, ExecutionControlCommand):
            return PreviewPreparation(
                None,
                CommandValidation.failed(
                    CommandValidationCode.UNSUPPORTED_COMMAND,
                    "执行控制命令不生成动作预览",
                ),
            )
        sequence, info, validation = self._expand(command)
        if not validation.is_valid:
            return PreviewPreparation(None, validation)
        return PreviewPreparation(
            self.register(
                sequence,
                source=source,
                plan=plan,
                command_info=info,
                validation=validation,
            ),
            validation,
        )

    def register(
        self,
        sequence: Sequence[SequenceEntry],
        *,
        source: str,
        plan: dict[str, Any],
        command_info: dict[str, Any],
        validation: CommandValidation,
    ) -> CommandPreview:
        if not sequence:
            raise ValueError("command preview sequence cannot be empty")
        if not validation.is_valid:
            raise ValueError("invalid sequence cannot become a preview")
        normalized_source = str(source).strip()
        if not normalized_source:
            raise ValueError("command preview source cannot be empty")
        now = self._clock()
        wall_now = self._wall_clock()
        serialized = tuple(_serialize_entry(item) for item in sequence)
        with self._lock:
            self._expire_unlocked(now)
            if self._current is not None and self._current.state is PreviewState.PENDING:
                self._current.state = PreviewState.SUPERSEDED
            self._version += 1
            self._current = _StoredPreview(
                preview_id=self._id_factory(),
                version=self._version,
                source=normalized_source,
                created_at=wall_now,
                expires_at=wall_now + self._preview_ttl_s,
                deadline=now + self._preview_ttl_s,
                state=PreviewState.PENDING,
                sequence=serialized,
                command_info=_copy_dict(command_info),
                plan=_copy_dict(plan),
                validation=_copy_dict(validation.to_dict()),
                risk=self._assess_risk(sequence),
            )
            return self._snapshot_unlocked(self._current)

    def current(self, *, expected_source: str | None = None) -> CommandPreview | None:
        with self._lock:
            self._expire_unlocked(self._clock())
            if self._current is None:
                return None
            if expected_source is not None and self._current.source != expected_source:
                return None
            return self._snapshot_unlocked(self._current)

    def pending(self, *, expected_source: str | None = None) -> CommandPreview | None:
        preview = self.current(expected_source=expected_source)
        if preview is None or preview.state is not PreviewState.PENDING:
            return None
        return preview

    def confirm(
        self,
        preview_id: str,
        version: int,
        *,
        risk_acknowledged: bool = False,
        expected_source: str | None = None,
    ) -> ConfirmedCommand:
        with self._lock:
            current = self._require_exact_unlocked(preview_id, version)
            if expected_source is not None and current.source != expected_source:
                raise PreviewSourceMismatchError(
                    "command preview belongs to another interaction surface"
                )
            self._expire_unlocked(self._clock())
            if current.state is PreviewState.EXPIRED:
                raise PreviewExpiredError("command preview has expired")
            if current.state is not PreviewState.PENDING:
                raise PreviewStateError(f"command preview is {current.state.value}")
            if current.risk.requires_acknowledgement and not risk_acknowledged:
                raise RiskAcknowledgementRequiredError(
                    "high-risk command requires explicit acknowledgement"
                )
            current.state = PreviewState.CONFIRMED
            return ConfirmedCommand(
                preview_id=current.preview_id,
                version=current.version,
                source=current.source,
                sequence=tuple(_deserialize_entry(item) for item in current.sequence),
            )

    def cancel_preview(
        self,
        preview_id: str | None = None,
        version: int | None = None,
        *,
        expected_source: str | None = None,
    ) -> bool:
        with self._lock:
            self._expire_unlocked(self._clock())
            current = self._current
            if current is None or current.state is not PreviewState.PENDING:
                return False
            if expected_source is not None and current.source != expected_source:
                return False
            if preview_id is not None and current.preview_id != preview_id:
                raise PreviewNotFoundError("command preview does not exist")
            if version is not None and (type(version) is not int or current.version != version):
                raise PreviewVersionConflictError(
                    "command preview version does not match"
                )
            current.state = PreviewState.CANCELLED
            return True

    def control_execution(
        self,
        action: ExecutionControlAction,
        *,
        expected_source: str | None = None,
    ) -> str:
        snapshot = self._execution.snapshot()
        if action is ExecutionControlAction.CANCEL:
            if snapshot.active:
                self._execution.cancel()
                return "execution_cancel_requested"
            if self.cancel_preview(expected_source=expected_source):
                return "preview_cancelled"
            return "nothing_to_cancel"
        if action is ExecutionControlAction.PAUSE:
            self._execution.pause()
            return "execution_paused"
        if action is ExecutionControlAction.RESUME:
            self._execution.resume()
            return "execution_resumed"
        raise ValueError(f"unsupported execution control: {action}")

    def status(self, *, expected_source: str | None = None) -> dict[str, Any]:
        preview = self.pending(expected_source=expected_source)
        execution = self._execution.snapshot()
        return {
            "preview": preview.to_dict() if preview else None,
            "execution": {
                "run_id": execution.run_id,
                "state": execution.state.value,
                "active": execution.active,
                "origin": execution.origin,
            },
        }

    def _expand(
        self,
        command: ActionCommand | SkillCommand | WorkflowCommand,
    ) -> tuple[list[SequenceEntry], dict[str, Any], CommandValidation]:
        if isinstance(command, ActionCommand):
            return self._expand_action(command)
        if isinstance(command, SkillCommand):
            return self._expand_skill(command)
        return self._expand_workflow(command)

    def _expand_action(
        self,
        command: ActionCommand,
    ) -> tuple[list[SequenceEntry], dict[str, Any], CommandValidation]:
        definition = None
        if command.action_id and not command.action_id.startswith("builtin."):
            try:
                definition = self._composition.get_action(command.action_id)
            except KeyError:
                return [], command.to_dict(), CommandValidation.failed(
                    CommandValidationCode.ACTION_NOT_FOUND,
                    f"动作不存在: {command.action_id}",
                )
            if definition.type is not command.action_type:
                return [], command.to_dict(), CommandValidation.failed(
                    CommandValidationCode.INVALID_ACTION_PARAMETERS,
                    "动作 ID 与动作类型不匹配",
                )
        parameters = (
            command.parameters
            if command.parameters or definition is None
            else definition.parameters
        )
        validation = validate_action_parameters(
            command.action_type,
            parameters,
            apply_defaults=True,
            reject_unknown=True,
        )
        if not validation.is_valid:
            return [], command.to_dict(), CommandValidation.failed(
                CommandValidationCode.INVALID_ACTION_PARAMETERS,
                validation.message,
            )
        action = ActionDefinition(
            id=command.action_id or f"planned.{command.action_type.name.lower()}",
            name=command.action_name or (definition.name if definition else "单步动作"),
            type=command.action_type,
            parameters=validation.parameters,
            robot_profile_id=self._robot_profile_id,
        )
        return (
            [SequenceItem.from_definition(action)],
            command.to_dict(),
            CommandValidation.succeeded("动作参数有效"),
        )

    def _expand_skill(
        self,
        command: SkillCommand,
    ) -> tuple[list[SequenceEntry], dict[str, Any], CommandValidation]:
        info = self._skill_engine.get_skill_info(command.skill_id) or command.to_dict()
        sequence, validation = self._skill_engine.parse_and_expand(
            SkillMatchResult(
                skill_id=command.skill_id,
                skill_name=str(info.get("name", command.skill_id)),
                confidence=1.0,
                extracted_params=command.parameters,
                reasoning="typed command",
            )
        )
        if not validation.is_valid:
            return [], info, CommandValidation.failed(
                CommandValidationCode.INVALID_SKILL,
                validation.message,
            )
        return list(sequence), info, CommandValidation.succeeded(validation.message)

    def _expand_workflow(
        self,
        command: WorkflowCommand,
    ) -> tuple[list[SequenceEntry], dict[str, Any], CommandValidation]:
        try:
            document = self._composition.load_workflow(command.workflow_name)
        except FileNotFoundError:
            return [], command.to_dict(), CommandValidation.failed(
                CommandValidationCode.WORKFLOW_NOT_FOUND,
                f"工作流不存在: {command.workflow_name}",
            )
        try:
            compiled = self._workflow_compiler.compile(document)
        except WorkflowCompilationError as exc:
            return [], command.to_dict(), CommandValidation.failed(
                CommandValidationCode.INVALID_WORKFLOW,
                str(exc),
            )
        if not compiled.entries:
            return [], command.to_dict(), CommandValidation.failed(
                CommandValidationCode.EMPTY_SEQUENCE,
                "工作流没有可执行节点",
            )
        return (
            list(compiled.entries),
            {
                **command.to_dict(),
                "workflow_id": document.workflow_id,
                "revision": document.revision,
            },
            CommandValidation.succeeded("工作流编译成功"),
        )

    def _require_exact_unlocked(self, preview_id: str, version: int) -> _StoredPreview:
        current = self._current
        if current is None or current.preview_id != str(preview_id):
            raise PreviewNotFoundError("command preview does not exist")
        if type(version) is not int or current.version != version:
            raise PreviewVersionConflictError("command preview version does not match")
        return current

    def _expire_unlocked(self, now: float) -> None:
        if (
            self._current is not None
            and self._current.state is PreviewState.PENDING
            and now >= self._current.deadline
        ):
            self._current.state = PreviewState.EXPIRED

    @classmethod
    def _assess_risk(cls, sequence: Sequence[SequenceEntry]) -> RiskAssessment:
        action_types = {
            item.definition.type
            for entry in sequence
            for item in _items(entry)
        }
        high = sorted(
            action_type.name
            for action_type in action_types & cls._HIGH_RISK_ACTIONS
        )
        if high:
            return RiskAssessment(
                RiskLevel.HIGH,
                tuple(f"physical_action:{name}" for name in high),
            )
        medium = sorted(
            action_type.name
            for action_type in action_types & cls._MEDIUM_RISK_ACTIONS
        )
        if medium:
            return RiskAssessment(
                RiskLevel.MEDIUM,
                tuple(f"environment_access:{name}" for name in medium),
            )
        return RiskAssessment(RiskLevel.LOW, ())

    @staticmethod
    def _snapshot_unlocked(stored: _StoredPreview) -> CommandPreview:
        return CommandPreview(
            preview_id=stored.preview_id,
            version=stored.version,
            source=stored.source,
            created_at=stored.created_at,
            expires_at=stored.expires_at,
            state=stored.state,
            sequence=tuple(_copy_dict(item) for item in stored.sequence),
            command_info=_copy_dict(stored.command_info),
            plan=_copy_dict(stored.plan),
            validation=_copy_dict(stored.validation),
            risk=stored.risk,
        )


def _items(entry: SequenceEntry) -> tuple[SequenceItem, ...]:
    if isinstance(entry, SequenceItem):
        return (entry,)
    if isinstance(entry, LoopBlock):
        return tuple(
            item
            for child in entry.items
            for item in _items(child)
        )
    if isinstance(entry, ParallelBlock):
        return tuple(
            item
            for branch in entry.branches
            for child in branch.items
            for item in _items(child)
        )
    raise TypeError(f"unsupported sequence entry: {type(entry).__name__}")


def _serialize_entry(entry: SequenceEntry) -> dict[str, Any]:
    return _copy_dict(entry.to_dict())


def _deserialize_entry(data: dict[str, Any]) -> SequenceEntry:
    return sequence_entry_from_dict(_copy_dict(data))


def _copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")
