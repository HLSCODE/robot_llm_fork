"""Unified command catalog and deterministic low-latency text resolver."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from ..configuration.settings import RuntimeSettings
from ..domain.commands import (
    ActionCommand,
    ExecutionControlAction,
    ExecutionControlCommand,
    PlannedCommand,
    SkillCommand,
    WorkflowCommand,
)
from ..domain.models import ActionType
from ..skill_system import SkillEngine
from .composition import CompositionService


class CommandResolutionStatus(str, Enum):
    MATCHED = "matched"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CommandResolution:
    status: CommandResolutionStatus
    command: PlannedCommand | None = None
    message: str = ""
    confidence: float = 0.0

    @property
    def should_fallback_to_llm(self) -> bool:
        return self.status is CommandResolutionStatus.NO_MATCH


class CommandCatalog:
    """Describe available commands and resolve only unambiguous expressions."""

    _CONTROL_ALIASES = {
        "停止任务": ExecutionControlAction.CANCEL,
        "取消任务": ExecutionControlAction.CANCEL,
        "暂停任务": ExecutionControlAction.PAUSE,
        "继续任务": ExecutionControlAction.RESUME,
        "恢复任务": ExecutionControlAction.RESUME,
    }
    _DISTANCE_PATTERN = re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>毫米|mm|厘米|cm)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        composition: CompositionService,
        skill_engine: SkillEngine,
        settings: RuntimeSettings,
    ) -> None:
        self._composition = composition
        self._skill_engine = skill_engine
        self._arm_step_mm = _positive(
            settings.command_arm_relative_step_mm,
            "command_arm_relative_step_mm",
        )
        self._arm_max_mm = _positive(
            settings.command_arm_relative_max_mm,
            "command_arm_relative_max_mm",
        )
        self._base_step_cm = _positive(
            settings.command_base_relative_step_cm,
            "command_base_relative_step_cm",
        )
        self._base_max_cm = _positive(
            settings.command_base_relative_max_cm,
            "command_base_relative_max_cm",
        )
        if self._arm_step_mm > self._arm_max_mm:
            raise ValueError("default arm relative step exceeds maximum")
        if self._base_step_cm > self._base_max_cm:
            raise ValueError("default base relative step exceeds maximum")

    def entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = [
            {
                "kind": "action",
                "id": "builtin.gripper",
                "name": "夹爪开关",
                "aliases": ["打开左夹爪", "关闭右夹爪"],
                "parameters": ["臂: 左或右", "操作: 开或关"],
            },
            {
                "kind": "action",
                "id": "builtin.arm_relative",
                "name": "机械臂有界相对移动",
                "aliases": ["左臂向前一点", "右臂向上10毫米"],
                "parameters": ["臂", "基座坐标系方向", "距离毫米"],
            },
            {
                "kind": "action",
                "id": "builtin.base_relative",
                "name": "底盘有界相对移动",
                "aliases": ["底盘向前一点", "底盘向左10厘米"],
                "parameters": ["方向", "距离厘米"],
            },
        ]
        entries.extend(
            {
                "kind": "action",
                "id": action.id,
                "name": action.name,
                "action_type": action.type.value,
                "aliases": [action.name],
                "parameters": action.parameters,
            }
            for action in self._composition.list_actions()
        )
        entries.extend(
            {"kind": "skill", **skill}
            for skill in self._skill_engine.list_all_skills()
        )
        entries.extend(
            {
                "kind": "workflow",
                "id": name,
                "name": name.removesuffix(".workflow.json"),
                "aliases": [name, name.removesuffix(".workflow.json")],
            }
            for name in self._composition.list_workflows()
        )
        entries.extend(
            {
                "kind": "execution_control",
                "id": action.value,
                "name": action.value,
                "aliases": [
                    alias
                    for alias, mapped in self._CONTROL_ALIASES.items()
                    if mapped is action
                ],
            }
            for action in ExecutionControlAction
        )
        return entries

    def resolve(self, text: str) -> CommandResolution:
        normalized = _normalize(text)
        if not normalized:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message="命令文本不能为空。",
            )
        control = self._CONTROL_ALIASES.get(normalized)
        if control is not None:
            return _matched(ExecutionControlCommand(control))

        mentions_gripper = "夹爪" in normalized
        mentions_base = "底盘" in normalized
        mentions_arm_motion = (
            not mentions_gripper
            and any(token in normalized for token in ("机械臂", "左臂", "右臂"))
        )
        if mentions_base and (mentions_gripper or mentions_arm_motion):
            return CommandResolution(
                CommandResolutionStatus.AMBIGUOUS,
                message="单次指令只能控制一个设备，请拆分底盘和机械臂/夹爪操作。",
            )

        gripper = self._resolve_gripper(normalized)
        if gripper.status is not CommandResolutionStatus.NO_MATCH:
            return gripper
        base = self._resolve_base_relative(normalized)
        if base.status is not CommandResolutionStatus.NO_MATCH:
            return base
        arm = self._resolve_arm_relative(normalized)
        if arm.status is not CommandResolutionStatus.NO_MATCH:
            return arm
        if _direction(normalized) is not None and (
            "一点" in normalized or self._DISTANCE_PATTERN.search(normalized)
        ):
            return CommandResolution(
                CommandResolutionStatus.AMBIGUOUS,
                message="请明确要移动底盘、左机械臂还是右机械臂。",
            )
        exact = self._resolve_exact_catalog(normalized)
        if len(exact) == 1:
            return _matched(exact[0])
        if len(exact) > 1:
            return CommandResolution(
                CommandResolutionStatus.AMBIGUOUS,
                message="命令名称匹配到多个 Action、Skill 或 Workflow，请使用唯一名称。",
            )
        return CommandResolution(CommandResolutionStatus.NO_MATCH)

    def _resolve_gripper(self, text: str) -> CommandResolution:
        if "夹爪" not in text:
            return CommandResolution(CommandResolutionStatus.NO_MATCH)
        operation = _single_choice(
            (("开", "打开", "张开", "松开"), ("关", "关闭", "闭合", "夹紧")),
            text,
        )
        if operation is None:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message="请明确夹爪需要打开还是关闭。",
            )
        arm_number = _arm_number(text)
        if arm_number is None:
            return CommandResolution(
                CommandResolutionStatus.AMBIGUOUS,
                message="存在左、右两个夹爪，请明确要控制左夹爪还是右夹爪。",
            )
        return _matched(
            ActionCommand(
                action_type=ActionType.MANIPULATE,
                action_id="builtin.gripper",
                action_name=f"{'左' if arm_number == 1 else '右'}夹爪{operation}",
                parameters={"执行器": "夹爪", "编号": arm_number, "操作": operation},
            )
        )

    def _resolve_arm_relative(self, text: str) -> CommandResolution:
        if not any(token in text for token in ("机械臂", "左臂", "右臂")):
            return CommandResolution(CommandResolutionStatus.NO_MATCH)
        direction = _direction(text)
        if direction is None:
            return CommandResolution(CommandResolutionStatus.NO_MATCH)
        arm_number = _arm_number(text)
        if arm_number is None:
            return CommandResolution(
                CommandResolutionStatus.AMBIGUOUS,
                message="存在左、右两个机械臂，请明确由哪一个机械臂移动。",
            )
        distance = self._distance(text, default=self._arm_step_mm, output_unit="mm")
        if distance is None:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message="机械臂相对移动距离无法识别。",
            )
        if distance > self._arm_max_mm:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message=f"机械臂单次相对移动不得超过 {self._arm_max_mm:g} 毫米。",
            )
        offsets = _arm_offsets(direction, distance)
        return _matched(
            ActionCommand(
                action_type=ActionType.MOVE,
                action_id="builtin.arm_relative",
                action_name=f"{'左' if arm_number == 1 else '右'}臂基座坐标相对移动",
                parameters={
                    "目标": "机械臂相对",
                    "臂": "左" if arm_number == 1 else "右",
                    "坐标系": "base",
                    "模式": "move_l",
                    **offsets,
                },
            )
        )

    def _resolve_base_relative(self, text: str) -> CommandResolution:
        if "底盘" not in text:
            return CommandResolution(CommandResolutionStatus.NO_MATCH)
        direction = _direction(text)
        if direction is None:
            return CommandResolution(CommandResolutionStatus.NO_MATCH)
        if direction not in {"forward", "backward", "left", "right"}:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message="底盘相对移动仅支持向前、向后、向左或向右。",
            )
        distance = self._distance(text, default=self._base_step_cm, output_unit="cm")
        if distance is None:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message="底盘相对移动距离无法识别。",
            )
        if distance > self._base_max_cm:
            return CommandResolution(
                CommandResolutionStatus.INVALID,
                message=f"底盘单次相对移动不得超过 {self._base_max_cm:g} 厘米。",
            )
        x_cm, y_cm = _base_offsets(direction, distance)
        return _matched(
            ActionCommand(
                action_type=ActionType.BASE_MOVE,
                action_id="builtin.base_relative",
                action_name="底盘相对移动",
                parameters={
                    "move_mode": "distance",
                    "x": x_cm,
                    "y": y_cm,
                    "angle": 0.0,
                },
            )
        )

    def _resolve_exact_catalog(self, text: str) -> list[PlannedCommand]:
        matches: list[PlannedCommand] = []
        for action in self._composition.list_actions():
            if text in {_normalize(action.id), _normalize(action.name)}:
                matches.append(ActionCommand(
                    action.type,
                    action.parameters,
                    action_id=action.id,
                    action_name=action.name,
                ))
        for skill in self._skill_engine.list_all_skills():
            aliases = {
                _normalize(str(value))
                for value in (
                    skill.get("id"),
                    skill.get("name"),
                    *(skill.get("examples") or []),
                    *(skill.get("tags") or []),
                )
                if value
            }
            if text in aliases:
                matches.append(SkillCommand(str(skill["id"]), {}))
        for workflow_name in self._composition.list_workflows():
            aliases = {
                _normalize(workflow_name),
                _normalize(workflow_name.removesuffix(".workflow.json")),
            }
            if text in aliases:
                matches.append(WorkflowCommand(workflow_name))
        return matches

    def _distance(self, text: str, *, default: float, output_unit: str) -> float | None:
        match = self._DISTANCE_PATTERN.search(text)
        if match is None:
            return default if "一点" in text else None
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        if output_unit == "mm":
            return value * 10.0 if unit in {"厘米", "cm"} else value
        return value / 10.0 if unit in {"毫米", "mm"} else value


def _matched(command: PlannedCommand) -> CommandResolution:
    return CommandResolution(
        CommandResolutionStatus.MATCHED,
        command=command,
        confidence=1.0,
    )


def _normalize(text: object) -> str:
    return "".join(str(text or "").strip().lower().split())


def _positive(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{label} must be numeric")
    normalized = float(value)
    if normalized <= 0:
        raise ValueError(f"{label} must be positive")
    return normalized


def _arm_number(text: str) -> int | None:
    left = any(token in text for token in ("左臂", "左机械臂", "左夹爪", "1号夹爪"))
    right = any(token in text for token in ("右臂", "右机械臂", "右夹爪", "2号夹爪"))
    if left == right:
        return None
    return 1 if left else 2


def _single_choice(groups: tuple[tuple[str, ...], ...], text: str) -> str | None:
    matches = [group[0] for group in groups if any(token in text for token in group)]
    return matches[0] if len(matches) == 1 else None


def _direction(text: str) -> str | None:
    matches = [
        direction
        for direction, aliases in (
            ("forward", ("向前", "前进")),
            ("backward", ("向后", "后退")),
            ("left", ("向左",)),
            ("right", ("向右",)),
            ("up", ("向上", "上升")),
            ("down", ("向下", "下降")),
        )
        if any(alias in text for alias in aliases)
    ]
    return matches[0] if len(matches) == 1 else None


def _arm_offsets(direction: str, distance_mm: float) -> dict[str, float]:
    axis, sign = {
        "forward": ("x_mm", 1.0),
        "backward": ("x_mm", -1.0),
        "left": ("y_mm", 1.0),
        "right": ("y_mm", -1.0),
        "up": ("z_mm", 1.0),
        "down": ("z_mm", -1.0),
    }[direction]
    return {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0, axis: sign * distance_mm}


def _base_offsets(direction: str, distance_cm: float) -> tuple[float, float]:
    return {
        "forward": (distance_cm, 0.0),
        "backward": (-distance_cm, 0.0),
        "left": (0.0, distance_cm),
        "right": (0.0, -distance_cm),
    }.get(direction, (0.0, 0.0))
