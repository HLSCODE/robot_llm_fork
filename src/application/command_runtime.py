"""Process-level command planning, approval, and execution control."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4

from ..domain.models import ActionType, SequenceItem
from ..skill_system import SkillEngine
from ..skill_system.models import SkillMatchResult, ValidationResult


class CommandRuntimeError(RuntimeError):
    """Base error for stable command approval failures."""

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


class ExecutionControlAction(str, Enum):
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"


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
    skill_info: dict[str, Any]
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
            "skill_info": _copy_dict(self.skill_info),
            "plan": _copy_dict(self.plan),
            "validation": _copy_dict(self.validation),
            "risk": self.risk.to_dict(),
            "requires_confirmation": True,
            "requires_risk_acknowledgement": (
                self.risk.requires_acknowledgement
            ),
        }


@dataclass(frozen=True, slots=True)
class PreviewPreparation:
    preview: CommandPreview | None
    validation: ValidationResult


@dataclass(frozen=True, slots=True)
class ConfirmedCommand:
    preview_id: str
    version: int
    source: str
    sequence: tuple[SequenceItem, ...]


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
    skill_info: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]
    risk: RiskAssessment


class CommandRuntime:
    """Own the only pending command preview and approval policy."""

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
        preview_ttl_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if preview_ttl_s <= 0:
            raise ValueError("preview_ttl_s must be positive")
        self._execution = execution
        self._skill_engine = skill_engine
        self._preview_ttl_s = float(preview_ttl_s)
        self._clock = clock
        self._wall_clock = wall_clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = RLock()
        self._version = 0
        self._current: _StoredPreview | None = None

    def list_skills(self) -> list[dict[str, Any]]:
        return self._skill_engine.list_all_skills()

    def prepare(
        self,
        match: SkillMatchResult,
        *,
        source: str,
        plan: dict[str, Any],
    ) -> PreviewPreparation:
        sequence, validation = self._skill_engine.parse_and_expand(match)
        if not validation.is_valid:
            return PreviewPreparation(None, validation)
        skill_info = self._skill_engine.get_skill_info(match.skill_id) or {}
        return PreviewPreparation(
            self.register(
                sequence,
                source=source,
                plan=plan,
                skill_info=skill_info,
                validation=validation,
            ),
            validation,
        )

    def register(
        self,
        sequence: list[SequenceItem],
        *,
        source: str,
        plan: dict[str, Any],
        skill_info: dict[str, Any],
        validation: ValidationResult,
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
        serialized = tuple(_copy_dict(item.to_dict()) for item in sequence)
        with self._lock:
            self._expire_unlocked(now)
            if (
                self._current is not None
                and self._current.state is PreviewState.PENDING
            ):
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
                skill_info=_copy_dict(skill_info),
                plan=_copy_dict(plan),
                validation=_copy_dict(validation.to_dict()),
                risk=self._assess_risk(sequence),
            )
            return self._snapshot_unlocked(self._current)

    def current(
        self,
        *,
        expected_source: str | None = None,
    ) -> CommandPreview | None:
        with self._lock:
            self._expire_unlocked(self._clock())
            if self._current is None:
                return None
            if (
                expected_source is not None
                and self._current.source != expected_source
            ):
                return None
            return self._snapshot_unlocked(self._current)

    def pending(
        self,
        *,
        expected_source: str | None = None,
    ) -> CommandPreview | None:
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
            if (
                expected_source is not None
                and current.source != expected_source
            ):
                raise PreviewSourceMismatchError(
                    "command preview belongs to another interaction surface"
                )
            self._expire_unlocked(self._clock())
            if current.state is PreviewState.EXPIRED:
                raise PreviewExpiredError("command preview has expired")
            if current.state is not PreviewState.PENDING:
                raise PreviewStateError(
                    f"command preview is {current.state.value}"
                )
            if (
                current.risk.requires_acknowledgement
                and not risk_acknowledged
            ):
                raise RiskAcknowledgementRequiredError(
                    "high-risk command requires explicit acknowledgement"
                )
            current.state = PreviewState.CONFIRMED
            return ConfirmedCommand(
                preview_id=current.preview_id,
                version=current.version,
                source=current.source,
                sequence=tuple(
                    SequenceItem.from_dict(_copy_dict(item))
                    for item in current.sequence
                ),
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
            if (
                expected_source is not None
                and current.source != expected_source
            ):
                return False
            if preview_id is not None and current.preview_id != preview_id:
                raise PreviewNotFoundError("command preview does not exist")
            if (
                version is not None
                and (
                    type(version) is not int
                    or current.version != version
                )
            ):
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

    def status(
        self,
        *,
        expected_source: str | None = None,
    ) -> dict[str, Any]:
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

    def _require_exact_unlocked(
        self,
        preview_id: str,
        version: int,
    ) -> _StoredPreview:
        current = self._current
        if current is None or current.preview_id != str(preview_id):
            raise PreviewNotFoundError("command preview does not exist")
        if type(version) is not int or current.version != version:
            raise PreviewVersionConflictError(
                "command preview version does not match"
            )
        return current

    def _expire_unlocked(self, now: float) -> None:
        if (
            self._current is not None
            and self._current.state is PreviewState.PENDING
            and now >= self._current.deadline
        ):
            self._current.state = PreviewState.EXPIRED

    @classmethod
    def _assess_risk(
        cls,
        sequence: list[SequenceItem],
    ) -> RiskAssessment:
        action_types = {item.definition.type for item in sequence}
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
            skill_info=_copy_dict(stored.skill_info),
            plan=_copy_dict(stored.plan),
            validation=_copy_dict(stored.validation),
            risk=stored.risk,
        )


def _copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    """Copy JSON-shaped command data without sharing mutable containers."""
    return copy.deepcopy(value)


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=UTC,
    ).isoformat().replace("+00:00", "Z")
