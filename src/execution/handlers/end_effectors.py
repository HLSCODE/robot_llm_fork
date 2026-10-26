"""Route tool commands once, then reuse existing device-specific operations."""

from __future__ import annotations

from typing import Callable
from functools import partial

from ...devices.end_effectors import end_effectors, ResolvedTool
from ...devices.end_effectors.providers import PositionedGripper, InitializableGripper, RotatingGripper
from ...devices.runtime.runtime import DeviceRuntime
from ..handler_api import (
    ActionCancelledError,
    ActionTimeoutError,
    ActionResultCode,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionParameters,
)


class ToolActionRouter:
    def __init__(self, runtime: DeviceRuntime, kind: str) -> None:
        self._runtime = runtime
        self._kind = kind

    def __call__(
        self, parameters: ActionParameters, context: ActionExecutionContext
    ) -> ActionHandlerResult:
        routing = end_effectors(self._runtime)
        target: ResolvedTool | None = None
        try:
            target = routing.from_parameters(self._kind, parameters)
            context.log(
                f"末端操作: arm={target.arm.value} tool_id={target.id} "
                f"provider={target.settings.provider} resources={target.resources}",
                "info",
            )
            if self._kind == "gripper":
                gripper = routing.gripper(target)
                operation: Callable[[], None]
                if parameters.get("操作") == "初始化":
                    if not isinstance(gripper, InitializableGripper):
                        raise ValueError("当前夹爪不支持显式初始化")
                    operation = gripper.initialize
                elif parameters.get("操作") in ("绝对旋转", "相对旋转", "停止旋转"):
                    if not isinstance(gripper, RotatingGripper):
                        raise ValueError("当前夹爪不支持旋转")
                    operation = gripper.stop_rotation if parameters["操作"] == "停止旋转" else partial(
                        gripper.rotate, float(parameters.get("旋转角度", 0)),
                        relative=parameters["操作"] == "相对旋转",
                        checkpoint=context.checkpoint,
                    )
                elif parameters.get("操作") == "定位":
                    if not isinstance(gripper, PositionedGripper):
                        raise ValueError("工具不支持开度定位")
                    percent = float(parameters.get("闭合度", 0))
                    if not 0 <= percent <= 100:
                        raise ValueError("闭合度必须为 0～100%")
                    operation = partial(gripper.move_closure, percent)
                elif parameters.get("操作", "开") == "开":
                    operation = gripper.open
                elif parameters.get("操作") == "关":
                    operation = gripper.grip
                else:
                    raise ValueError("未知夹爪操作")
                context.invoke("gripper.execute", operation)
                return context.success(operation="gripper.execute", device_id=target.device_id)
            if self._kind == "pipette" and parameters.get("操作") == "初始化":
                context.invoke("pipette.initialize", partial(routing.initialize_pipette, target))
                return context.success(operation="pipette.initialize", device_id=target.device_id)
            # Keep existing command semantics, but retain the actual tool ID in
            # failure diagnostics as well as successful results.
            from .manipulation import PipetteActionHandler, ToolChangerActionHandler

            handler = (
                PipetteActionHandler(self._runtime, device_id=target.device_id)
                if self._kind == "pipette"
                else ToolChangerActionHandler(self._runtime, device_id=target.device_id)
            )
            return handler(parameters, context)
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as error:
            return context.failure(
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"末端设备操作失败: {error}",
                operation=f"{self._kind}.execute",
                device_id=target.device_id if target is not None else self._kind,
                error=error,
            )
