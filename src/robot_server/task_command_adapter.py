"""固定任务命令的运行时适配器。

任务文件始终只作为模板读取；本模块对其内存副本替换参数，交给执行器后即丢弃。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from uuid import uuid4

from ..core.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ..core.pose_compensation import parse_pose
from ..core.storage import StorageManager


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 点位单独保存，标定时只需更新 JSON；每次注液任务开始都会重新读取。
DEFAULT_POINTS_FILE = _PROJECT_ROOT / "config" / "injection_points.json"

# command_type 不是任意任务文件名：白名单避免远程客户端执行非预期任务。
ALLOWED_COMMANDS = frozenset(
    {
        "730-1-2",
        "730-peiye",
        "730-2-3",
        "730-zhuye",
        "730-3-1",
    }
)

_HEIGHT_LEVELS = frozenset({"upper", "middle", "lower"})
_DISPENSE_METHODS = frozenset({"vertical", "circular"})
_CIRCULAR_ACTION_ID = "f8a20c9c-ba91-4088-b61c-e12a77658df3"


class TaskCommandError(ValueError):
    """A stable, client-facing command preparation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PreparedTaskCommand:
    request_id: str
    command_type: str
    task_filename: str
    entries: list[SequenceEntry]
    total_steps: int


class TaskCommandAdapter:
    """Load, validate and patch one of the five fixed task templates in memory."""

    def __init__(
        self,
        *,
        storage: type[StorageManager] = StorageManager,
        points_file: Path | str = DEFAULT_POINTS_FILE,
    ) -> None:
        self._storage = storage
        self._points_file = Path(points_file)

    @staticmethod
    def request_id(payload: dict[str, Any]) -> str:
        value = payload.get("request_id")
        if value is None or value == "":
            return str(uuid4())
        if not isinstance(value, str):
            raise TaskCommandError("INVALID_ARGUMENT", "request_id 必须是字符串")
        return value

    def prepare(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> PreparedTaskCommand:
        # 以下流程全部在执行器启动前完成，任一校验失败都不会产生机器人动作。
        request_id = request_id or self.request_id(payload)
        command_type = self._normalize_command_type(payload.get("command_type"))
        task_filename = f"{command_type}.task"
        entries = self._load_task(task_filename)

        # 只有这两个固定任务包含需要由客户端字段覆盖的动作。
        if command_type == "730-peiye":
            self._patch_prepare_solution(entries, payload)
        elif command_type == "730-zhuye":
            self._patch_dispense(entries, payload)

        # 模板可能保留了上一次 GUI 执行的状态，远程执行前统一重置。
        self._reset_statuses(entries)
        self._validate_all_move_poses(entries)
        return PreparedTaskCommand(
            request_id=request_id,
            command_type=command_type,
            task_filename=task_filename,
            entries=entries,
            total_steps=self._total_steps(entries),
        )

    @staticmethod
    def _normalize_command_type(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TaskCommandError("INVALID_ARGUMENT", "command_type 必须是非空字符串")

        command_type = value.strip()
        if "/" in command_type or "\\" in command_type or Path(command_type).name != command_type:
            raise TaskCommandError("UNKNOWN_COMMAND", "command_type 不能包含目录或路径字符")
        if command_type.endswith(".task"):
            command_type = command_type[:-5]
        if command_type not in ALLOWED_COMMANDS:
            raise TaskCommandError("UNKNOWN_COMMAND", f"不支持的 command_type: {command_type}")
        return command_type

    def _load_task(self, task_filename: str) -> list[SequenceEntry]:
        task_path = self._storage.TASKS_DIR / task_filename
        if not task_path.is_file():
            raise TaskCommandError("TASK_NOT_FOUND", f"任务不存在: {task_filename}")
        try:
            entries = self._storage.load_entries(task_filename)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TaskCommandError(
                "TEMPLATE_INVALID",
                f"任务模板解析失败: {task_filename}: {exc}",
            ) from exc
        if not entries:
            raise TaskCommandError("TEMPLATE_INVALID", f"任务为空: {task_filename}")
        return entries

    def _patch_prepare_solution(
        self,
        entries: Sequence[SequenceEntry],
        payload: dict[str, Any],
    ) -> None:
        # 同名 XI200 都代表本次配液的吸液步骤，必须全部同步修改。
        targets = [item for item in self._iter_items(entries) if item.definition.name == "XI200"]
        if not targets:
            raise TaskCommandError("TEMPLATE_INVALID", "730-peiye.task 中找不到 XI200 动作")

        for item in targets:
            params = item.definition.parameters
            if (
                item.definition.type is not ActionType.MANIPULATE
                or params.get("执行器") != "吸液枪"
                or params.get("操作") != "吸"
            ):
                raise TaskCommandError(
                    "TEMPLATE_INVALID",
                    f"XI200 动作结构不正确: {item.uuid}",
                )
            self._rounded_device_value(
                params.get("容量"),
                field=f"XI200[{item.uuid}].容量",
                minimum=1,
                maximum=65535,
                error_code="TEMPLATE_INVALID",
            )

        # 未传参数时保留任务中每个 XI200 各自的默认容量。
        if "aspirate_volume_ml" not in payload:
            return
        capacity = self._rounded_device_value(
            payload["aspirate_volume_ml"],
            field="aspirate_volume_ml",
            minimum=1,
            maximum=65535,
        )
        for item in targets:
            item.definition.parameters["容量"] = capacity

    def _patch_dispense(
        self,
        entries: Sequence[SequenceEntry],
        payload: dict[str, Any],
    ) -> None:
        # 缺省值与 730-zhuye.task 当前示教的工位 1 / upper / vertical 一致。
        station_id = self._station_id(payload.get("station_id", 1))
        height_level = self._enum_value(
            payload.get("height_level", "upper"),
            field="height_level",
            allowed=_HEIGHT_LEVELS,
        )
        method = self._enum_value(
            payload.get("method", "vertical"),
            field="method",
            allowed=_DISPENSE_METHODS,
        )

        points = self._load_points()
        station_points = points[str(station_id)]
        shang_pose = station_points["shang"]
        height_pose = station_points[height_level]

        # 任务模板只保留工位 1 的三个“槽位”；实际工位由下方点位覆盖决定。
        shang_targets = [
            item for item in self._iter_items(entries) if item.definition.name == "730-1-shang"
        ]
        high_targets = [
            item for item in self._iter_items(entries) if item.definition.name == "730-1-high"
        ]
        dispense_targets = [
            item for item in self._iter_items(entries) if item.definition.name == "tuye"
        ]
        if len(shang_targets) != 2 or len(high_targets) != 1 or len(dispense_targets) != 1:
            raise TaskCommandError(
                "TEMPLATE_INVALID",
                "730-zhuye.task 必须包含两处 730-1-shang、一处 730-1-high 和一处 tuye",
            )

        for item in (*shang_targets, *high_targets):
            if item.definition.type is not ActionType.MOVE:
                raise TaskCommandError(
                    "TEMPLATE_INVALID",
                    f"点位动作类型不正确: {item.definition.name}",
                )
        # 两处上方点位都要替换：一处用于接近，另一处用于注液后的撤离。
        for item in shang_targets:
            item.definition.parameters["点位"] = self._pose_text(shang_pose)
        high_targets[0].definition.parameters["点位"] = self._pose_text(height_pose)

        dispense_item = dispense_targets[0]
        dispense_params = dispense_item.definition.parameters
        if (
            dispense_item.definition.type is not ActionType.MANIPULATE
            or dispense_params.get("执行器") != "吸液枪"
            or dispense_params.get("操作") != "吐"
        ):
            raise TaskCommandError("TEMPLATE_INVALID", "tuye 动作结构不正确")

        if "flow_rate_ml_min" in payload:
            dispense_speed = self._rounded_device_value(
                payload["flow_rate_ml_min"],
                field="flow_rate_ml_min",
                minimum=1,
                maximum=9999,
            )
        else:
            dispense_speed = self._rounded_device_value(
                dispense_params.get("吐液速度", 800),
                field="tuye.吐液速度",
                minimum=1,
                maximum=9999,
                error_code="TEMPLATE_INVALID",
            )

        if "volume_ml" in payload:
            dispense_volume = self._rounded_device_value(
                payload["volume_ml"],
                field="volume_ml",
                minimum=1,
                maximum=65535,
            )
        else:
            dispense_volume = self._rounded_device_value(
                dispense_params.get("容量", 500),
                field="tuye.容量",
                minimum=1,
                maximum=65535,
                error_code="TEMPLATE_INVALID",
            )

        if method == "vertical":
            # 垂直注液沿用任务原 tuye 动作，仅覆盖客户端明确给出的字段。
            if "flow_rate_ml_min" in payload:
                dispense_params["吐液速度"] = dispense_speed
            if "volume_ml" in payload:
                dispense_params["容量"] = dispense_volume
            return

        # 转圈注液保持原 SequenceItem 的 UUID，便于沿用步骤事件索引与执行状态。
        dispense_item.definition = ActionDefinition(
            id=_CIRCULAR_ACTION_ID,
            name="zhuanquanzhuye",
            type=ActionType.MANIPULATE,
            parameters={
                "执行器": "右臂转圈注液",
                "位姿": self._pose_text(height_pose),
                "半径R": 5.0,
                "吐液速度": dispense_speed,
                "吐液量": dispense_volume,
                "圈数": 1.0,
                "分段数": 72,
                "过渡半径": 10,
                "运动速度": 10,
                "连续运动": True,
                "顺时针": False,
            },
        )

    def _load_points(self) -> dict[str, dict[str, list[float]]]:
        try:
            with self._points_file.open("r", encoding="utf-8") as file:
                raw = json.load(file)
        except (OSError, ValueError, TypeError) as exc:
            raise TaskCommandError(
                "POINT_CONFIG_INVALID",
                f"注液点位配置读取失败: {exc}",
            ) from exc

        # 配置严格要求 4 个工位、每工位 4 个语义点位，防止半份标定被误用。
        result: dict[str, dict[str, list[float]]] = {}
        try:
            if set(raw) != {"1", "2", "3", "4"}:
                raise ValueError("必须包含工位 1、2、3、4")
            for station_id, station_points in raw.items():
                if set(station_points) != {"shang", "upper", "middle", "lower"}:
                    raise ValueError(f"工位 {station_id} 的点位键不完整")
                result[station_id] = {}
                for key, pose in station_points.items():
                    result[station_id][key] = self._finite_pose(
                        pose,
                        label=f"工位 {station_id}.{key}",
                    )
        except (TypeError, ValueError) as exc:
            raise TaskCommandError("POINT_CONFIG_INVALID", str(exc)) from exc
        return result

    @staticmethod
    def _station_id(value: Any) -> int:
        if isinstance(value, bool):
            raise TaskCommandError("INVALID_ARGUMENT", "station_id 必须是 1、2、3 或 4")
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise TaskCommandError(
                "INVALID_ARGUMENT",
                "station_id 必须是 1、2、3 或 4",
            ) from exc
        if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
            raise TaskCommandError("INVALID_ARGUMENT", "station_id 必须是 1、2、3 或 4")
        station_id = int(decimal_value)
        if station_id not in {1, 2, 3, 4}:
            raise TaskCommandError("INVALID_ARGUMENT", "station_id 必须是 1、2、3 或 4")
        return station_id

    @staticmethod
    def _enum_value(value: Any, *, field: str, allowed: frozenset[str]) -> str:
        if not isinstance(value, str) or value not in allowed:
            choices = "|".join(sorted(allowed))
            raise TaskCommandError("INVALID_ARGUMENT", f"{field} 必须是 {choices}")
        return value

    @staticmethod
    def _rounded_device_value(
        value: Any,
        *,
        field: str,
        minimum: int,
        maximum: int,
        error_code: str = "INVALID_ARGUMENT",
    ) -> int:
        if isinstance(value, bool):
            raise TaskCommandError(error_code, f"{field} 必须是数字")
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise TaskCommandError(error_code, f"{field} 必须是数字") from exc
        if not decimal_value.is_finite():
            raise TaskCommandError(error_code, f"{field} 必须是有限数字")
        # Decimal 的 ROUND_HALF_UP 符合操作者通常理解的“四舍五入”，避免 Python round 的银行家舍入。
        rounded = int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if rounded < minimum or rounded > maximum:
            raise TaskCommandError(
                error_code,
                f"{field} 四舍五入后必须在 {minimum}..{maximum} 范围内",
            )
        return rounded

    @classmethod
    def _validate_all_move_poses(cls, entries: Sequence[SequenceEntry]) -> None:
        # 在任何一步运动前验证整份任务，避免中途才因为损坏点位停机。
        for item in cls._iter_items(entries):
            if item.definition.type is not ActionType.MOVE:
                continue
            params = item.definition.parameters
            if params.get("目标", "机械臂") == "身体":
                continue
            try:
                cls._finite_pose(params.get("点位"), label=item.definition.name)
            except (TypeError, ValueError) as exc:
                raise TaskCommandError(
                    "TEMPLATE_INVALID",
                    f"动作 {item.definition.name} 的点位无效: {exc}",
                ) from exc

    @staticmethod
    def _finite_pose(value: Any, *, label: str) -> list[float]:
        pose = parse_pose(value)
        if not all(math.isfinite(number) for number in pose):
            raise ValueError(f"{label} 必须包含六个有限数字")
        return pose

    @staticmethod
    def _pose_text(pose: Sequence[float]) -> str:
        return json.dumps(list(pose), ensure_ascii=False)

    @staticmethod
    def _iter_items(entries: Iterable[SequenceEntry]) -> Iterator[SequenceItem]:
        for entry in entries:
            if isinstance(entry, LoopBlock):
                yield from entry.items
            elif isinstance(entry, SequenceItem):
                yield entry

    @classmethod
    def _reset_statuses(cls, entries: Sequence[SequenceEntry]) -> None:
        for entry in entries:
            if isinstance(entry, LoopBlock):
                entry.current_iteration = 0
                for item in entry.items:
                    item.status = SequenceItemStatus.PENDING
            elif isinstance(entry, SequenceItem):
                entry.status = SequenceItemStatus.PENDING

    @staticmethod
    def _total_steps(entries: Sequence[SequenceEntry]) -> int:
        return sum(
            len(entry.items) * entry.repeat_count if isinstance(entry, LoopBlock) else 1
            for entry in entries
        )
