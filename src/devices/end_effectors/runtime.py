"""Resolve a requested capability and mount before touching hardware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...configuration.end_effectors import EndEffectorSettings, EndEffectorTool
from ..runtime.arm_models import ArmId
from ..runtime.runtime import DeviceRuntime
from ..runtime.ids import ROBOT_SYSTEM
from ..runtime.models import DeviceCapability, DeviceContractError
from .providers import Gripper, PositionedGripper


@dataclass(frozen=True)
class ResolvedTool:
    id: str
    arm: ArmId
    kind: str
    device_id: str
    resources: tuple[str, ...]
    settings: EndEffectorTool


class EndEffectorRuntime:
    def __init__(self, runtime: DeviceRuntime, settings: EndEffectorSettings) -> None:
        self.runtime = runtime
        self.settings = settings
        self._tools = {tool.id: tool for tool in settings.tools}

    def resolve(
        self,
        kind: str,
        *,
        arm: str | ArmId | None = None,
        tool_id: str = "",
    ) -> ResolvedTool:
        parsed_arm = (
            arm if isinstance(arm, ArmId) else (ArmId.parse(arm) if arm is not None else None)
        )
        if not self.settings.configured:
            raise ValueError("未配置末端设备，请在 [end_effectors] 中声明工具与机械臂绑定")
        if arm is None and not tool_id:
            raise ValueError("必须明确指定末端机械臂或工具实例")
        candidates: list[tuple[ArmId, EndEffectorTool, str]] = []
        for mount in self.settings.mounts:
            if parsed_arm is not None and mount.arm != parsed_arm.value:
                continue
            if mount.mode == "none":
                continue
            ids = (
                (mount.tool,)
                if mount.mode == "fixed"
                else ((mount.changer,) if kind == "tool_changer" else mount.tools)
            )
            for candidate_id in ids:
                tool = self._tools[candidate_id]
                if tool.kind != kind or (tool_id and tool.id != tool_id):
                    continue
                if mount.mode == "exchangeable" and kind != "tool_changer" and not tool_id:
                    continue
                candidates.append((ArmId.parse(mount.arm), tool, mount.mode))
        if len(candidates) != 1:
            raise ValueError(
                f"无法唯一解析末端设备: 类型={kind}, 臂={arm}, 工具={tool_id or '未指定'}；请配置绑定或明确选择工具"
            )
        logical_arm, tool, _ = candidates[0]
        resources = [f"end-effector:{logical_arm.value}", f"tool:{tool.id}"]
        if tool.connection:
            resources.append(f"connection:{tool.connection}")
        if tool.provider == "robot":
            reason = self.unavailable_reason(tool.id)
            if reason:
                raise DeviceContractError(reason)
            resources.append(ROBOT_SYSTEM)
        return ResolvedTool(tool.id, logical_arm, kind, f"tool:{tool.id}", tuple(resources), tool)

    def unavailable_reason(self, tool_id: str) -> str:
        tool = self._tools[tool_id]
        if tool.provider == "robot" and ROBOT_SYSTEM not in self.runtime.registered_device_ids():
            return f"工具 {tool_id} 不可用：机械臂运行时尚未注册"
        if (
            tool.provider == "robot"
            and DeviceCapability.GRIPPER not in self.runtime.snapshot(ROBOT_SYSTEM).capabilities
        ):
            return f"工具 {tool_id} 不可用：当前 [robot].provider 未提供内置夹爪能力，请选择外接夹爪或 mode='none'"
        return ""

    def from_parameters(self, kind: str, parameters: Mapping[str, Any]) -> ResolvedTool:
        from ...domain.action_schema import PENDING_TARGET_FIELDS

        if PENDING_TARGET_FIELDS in parameters:
            raise ValueError("动作目标待配置，请先编辑动作并选择机械臂和工具")
        if "编号" in parameters or parameters.get("执行器") == "知行夹爪":
            raise ValueError("旧末端动作格式已移除，请使用 arm/tool_id 与通用夹爪动作")
        return self.resolve(
            kind, arm=parameters.get("arm") or None, tool_id=str(parameters.get("tool_id") or "")
        )

    def device(self, target: ResolvedTool) -> Any:
        return self.runtime.require(target.device_id)

    def initialize_pipette(self, target: ResolvedTool) -> bool:
        """Explicit recovery only; the caller already owns target resources."""
        if target.kind != "pipette":
            raise ValueError("目标工具不是吸液枪")
        connection = next(
            c for c in self.settings.connections if c.id == target.settings.connection
        )
        self.runtime.require("end-effector-connections").reconnect(connection.port)
        if self.device(target).initialize() is not True:
            raise RuntimeError("吸液枪初始化未确认")
        return True

    def gripper(self, target: ResolvedTool) -> Gripper:
        device = self.device(target)
        if not isinstance(device, Gripper):
            raise TypeError(f"工具 {target.id} 不支持夹爪操作")
        return device

    def move_position(self, target: ResolvedTool, position: int) -> None:
        if not 0 <= position <= 1000:
            raise ValueError("夹爪位置必须为 0～1000")
        gripper = self.gripper(target)
        if not isinstance(gripper, PositionedGripper):
            raise ValueError("该夹爪不支持位置控制")
        gripper.move_closure((1000 - position) / 10)

    def choices(self, kind: str, arm: str | None = None) -> list[tuple[str, str]]:
        values = []
        for tool in self.settings.tools:
            if tool.kind != kind:
                continue
            try:
                self.resolve(kind, arm=arm, tool_id=tool.id)
            except (ValueError, DeviceContractError):
                continue
            values.append((tool.id, tool.label))
        labels = [label for _, label in values]
        values = [(identity, f"{label}（{identity}）" if labels.count(label) > 1 else label) for identity, label in values]
        return values


def end_effectors(runtime: DeviceRuntime) -> EndEffectorRuntime:
    configured = getattr(runtime, "end_effectors", None)
    return (
        configured
        if isinstance(configured, EndEffectorRuntime)
        else EndEffectorRuntime(runtime, EndEffectorSettings())
    )
