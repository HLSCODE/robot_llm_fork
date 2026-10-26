"""Application operations for explicit tool selections and observed changer state."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar
from uuid import uuid4

from ..devices import DeviceRuntime, ResourceArbiter
from ..devices.end_effectors import end_effectors, ResolvedTool
from ..devices.end_effectors.providers import PositionedGripper
from ..devices.runtime.errors import normalize_device_error

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    """Configured capabilities only; reading this never opens a device."""

    label: str
    details: str
    operations: tuple[str, ...]
    rotation_min: float = -32768
    rotation_max: float = 32767


_PROVIDER_OPERATIONS = {
    "robot": ("开", "关", "定位"),
    "zhixing": ("开", "关", "定位"),
    "dh_rgd": ("开", "关", "定位", "初始化", "绝对旋转", "相对旋转", "停止旋转"),
    "adp": ("初始化", "退枪头"),
    "kuaihuanshou": ("关", "开"),
}


class EndEffectorService:
    def __init__(self, runtime: DeviceRuntime, resources: ResourceArbiter) -> None:
        self._runtime = runtime
        self._resources = resources
        self.routing = end_effectors(runtime)

    def operate(self, target: ResolvedTool, operation: str, callback: Callable[[], T]) -> T:
        with self._resources.acquire(f"end-effector:{operation}:{uuid4().hex}", target.resources):
            try:
                return callback()
            except Exception as error:
                raise normalize_device_error(
                    error, device_id=target.device_id, operation=operation
                ) from error

    def set_gripper(
        self,
        arm: str,
        *,
        tool_id: str = "",
        opened: bool | None = None,
        position: int | None = None,
        closure_percent: float | None = None,
    ) -> None:
        if sum(v is not None for v in (opened, position, closure_percent)) != 1:
            raise ValueError("必须指定且仅指定一个夹爪操作")
        target = self.routing.resolve("gripper", arm=arm, tool_id=tool_id)

        def execute() -> None:
            if position is not None:
                self.routing.move_position(target, position)
                return
            gripper = self.routing.gripper(target)
            if closure_percent is not None:
                if not isinstance(gripper, PositionedGripper):
                    raise ValueError("当前夹爪不支持开度定位")
                gripper.move_closure(closure_percent)
            elif opened:
                gripper.open()
            else:
                gripper.grip()

        self.operate(target, "gripper.set", execute)

    def choices(self, kind: str, arm: str | None = None) -> list[tuple[str, str]]:
        return self.routing.choices(kind, arm)

    def describe_tool(self, tool_id: str) -> ToolPresentation | None:
        tool = next((tool for tool in self.routing.settings.tools if tool.id == tool_id), None)
        if tool is None:
            return None
        scale = tool.rotation_units_per_degree if tool.provider == "dh_rgd" else 1.0
        return ToolPresentation(
            tool.label,
            f"ID：{tool.id}\nProvider：{tool.provider}\n连接：{tool.connection or '机械臂 SDK'}\n站号：{tool.station}\n配置的工具实例，不代表已安装或在线",
            _PROVIDER_OPERATIONS[tool.provider], -32768 / scale, 32767 / scale,
        )

    def read_changer_states(self) -> list[dict[str, Any]]:
        """Background caller only. No tool identity or installation inference."""
        states = []
        for mount in self.routing.settings.mounts:
            if mount.mode != "exchangeable":
                continue
            target = self.routing.resolve("tool_changer", arm=mount.arm)
            state: dict[str, Any] = {
                "arm": mount.arm,
                "tool_id": target.id,
                "locked": "unknown",
                "power": "unknown",
                "sampled_at": None,
                "error": "",
            }
            try:
                changer = self.routing.device(target)
                state.update(changer.read_status())
                state["sampled_at"] = time.time()
            except Exception as error:
                state["error"] = str(error)
            states.append(state)
        return states
