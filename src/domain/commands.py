"""Typed interaction commands shared by every presentation surface."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TypeAlias

from .models import ActionType


class CommandKind(str, Enum):
    ACTION = "action"
    SKILL = "skill"
    WORKFLOW = "workflow"
    EXECUTION_CONTROL = "execution_control"


class ExecutionControlAction(str, Enum):
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"


@dataclass(frozen=True, slots=True)
class ActionCommand:
    action_type: ActionType
    parameters: dict[str, Any]
    action_id: str = ""
    action_name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, ActionType):
            raise TypeError("action_type must be an ActionType")
        if not isinstance(self.parameters, dict):
            raise TypeError("action command parameters must be an object")
        object.__setattr__(self, "parameters", deepcopy(self.parameters))

    @property
    def kind(self) -> CommandKind:
        return CommandKind.ACTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "action_type": self.action_type.value,
            "action_id": self.action_id,
            "action_name": self.action_name,
            "parameters": deepcopy(self.parameters),
        }


@dataclass(frozen=True, slots=True)
class SkillCommand:
    skill_id: str
    parameters: dict[str, Any]

    def __post_init__(self) -> None:
        _require_text(self.skill_id, "skill_id")
        if not isinstance(self.parameters, dict):
            raise TypeError("skill command parameters must be an object")
        object.__setattr__(self, "parameters", deepcopy(self.parameters))

    @property
    def kind(self) -> CommandKind:
        return CommandKind.SKILL

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "skill_id": self.skill_id,
            "parameters": deepcopy(self.parameters),
        }


@dataclass(frozen=True, slots=True)
class WorkflowCommand:
    workflow_name: str

    def __post_init__(self) -> None:
        _require_text(self.workflow_name, "workflow_name")

    @property
    def kind(self) -> CommandKind:
        return CommandKind.WORKFLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "workflow_name": self.workflow_name,
        }


@dataclass(frozen=True, slots=True)
class ExecutionControlCommand:
    action: ExecutionControlAction

    def __post_init__(self) -> None:
        if not isinstance(self.action, ExecutionControlAction):
            raise TypeError("execution control action is invalid")

    @property
    def kind(self) -> CommandKind:
        return CommandKind.EXECUTION_CONTROL

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "action": self.action.value}


PlannedCommand: TypeAlias = (
    ActionCommand | SkillCommand | WorkflowCommand | ExecutionControlCommand
)


def command_from_dict(data: Mapping[str, Any]) -> PlannedCommand:
    """Parse an untrusted planner object and reject unknown command shapes."""
    if not isinstance(data, Mapping):
        raise TypeError("command must be a JSON object")
    try:
        kind = CommandKind(data["kind"])
    except KeyError as exc:
        raise ValueError("command kind is required") from exc
    if kind is CommandKind.ACTION:
        _require_keys(
            data,
            required={"kind", "action_type"},
            optional={"action_id", "action_name", "parameters"},
        )
        parameters = _parameters(data)
        return ActionCommand(
            action_type=ActionType(data["action_type"]),
            action_id=str(data.get("action_id", "")).strip(),
            action_name=str(data.get("action_name", "")).strip(),
            parameters=parameters,
        )
    if kind is CommandKind.SKILL:
        _require_keys(
            data,
            required={"kind", "skill_id"},
            optional={"parameters"},
        )
        return SkillCommand(
            skill_id=str(data["skill_id"]).strip(),
            parameters=_parameters(data),
        )
    if kind is CommandKind.WORKFLOW:
        _require_keys(data, required={"kind", "workflow_name"})
        return WorkflowCommand(str(data["workflow_name"]).strip())
    if kind is CommandKind.EXECUTION_CONTROL:
        _require_keys(data, required={"kind", "action"})
        return ExecutionControlCommand(ExecutionControlAction(data["action"]))
    raise AssertionError(f"unhandled command kind: {kind}")


def _parameters(data: Mapping[str, Any]) -> dict[str, Any]:
    raw = data.get("parameters", {})
    if not isinstance(raw, dict):
        raise TypeError("command parameters must be a JSON object")
    return deepcopy(raw)


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _require_keys(
    data: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    keys = set(data)
    missing = required - keys
    if missing:
        raise ValueError(
            "command is missing fields: " + ", ".join(sorted(missing))
        )
    unexpected = keys - required - (optional or set())
    if unexpected:
        raise ValueError(
            "command has unknown fields: " + ", ".join(sorted(unexpected))
        )
