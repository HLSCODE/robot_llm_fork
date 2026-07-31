from __future__ import annotations

from ...device_runtime import DeviceRuntime, Pipette, ToolRackControl
from ...device_runtime.ids import PIPETTE, ROBOT_SYSTEM
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionParameters,
    ActionResultCode,
    ActionTimeoutError,
)


class ChangeToolActionHandler:
    """Attach or detach one configured tool-rack slot."""

    _OPERATION = "tool_rack.change_tool"

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            slot = int(parameters.get("Gun_Position", 1))
            operation = str(parameters.get("Operation", "取")).strip()
        except (TypeError, ValueError) as exc:
            message = f"换枪参数无效: {exc}"
            return context.failure(
                ActionResultCode.INVALID_PARAMETERS,
                message,
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        context.log(
            f"换枪动作: 枪位={slot}, 操作={operation}",
            "info",
        )
        if slot not in (1, 2) or operation not in ("取", "放"):
            message = (
                f"未知的换枪参数组合: 枪位={slot}, 操作={operation}"
            )
            return context.failure(
                ActionResultCode.INVALID_PARAMETERS,
                message,
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        try:
            tool_rack = self._device_runtime.require(
                ROBOT_SYSTEM,
                ToolRackControl,
            )
        except Exception as exc:
            message = f"工具架设备不可用: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_UNAVAILABLE,
                message,
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        try:
            eject_tool = None
            if operation == "放":
                pipette = self._device_runtime.require(PIPETTE, Pipette)
                eject_tool = pipette.eject_tip
            context.invoke(
                self._OPERATION,
                lambda: tool_rack.change_tool(
                    slot,
                    attach=operation == "取",
                    eject_tool=eject_tool,
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            message = f"执行换枪出错: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_OPERATION_FAILED,
                message,
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        context.log(
            f"工具架操作完成: slot={slot}, operation={operation}",
            "info",
        )
        return context.success(
            operation=self._OPERATION,
            device_id=ROBOT_SYSTEM,
        )
