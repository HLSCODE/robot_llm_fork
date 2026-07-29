from __future__ import annotations

from ...device_runtime import DeviceRuntime, ToolRackControl
from ...device_runtime.ids import ROBOT_SYSTEM
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionParameters,
    ActionTimeoutError,
)


class ChangeToolActionHandler:
    """Attach or detach one configured tool-rack slot."""

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        try:
            slot = int(parameters.get("Gun_Position", 1))
            operation = str(parameters.get("Operation", "取")).strip()
        except (TypeError, ValueError) as exc:
            context.log(f"换枪参数无效: {exc}", "error")
            return False

        context.log(
            f"换枪动作: 枪位={slot}, 操作={operation}",
            "info",
        )
        if slot not in (1, 2) or operation not in ("取", "放"):
            context.log(
                f"未知的换枪参数组合: 枪位={slot}, 操作={operation}",
                "error",
            )
            return False

        try:
            tool_rack = self._device_runtime.require(
                ROBOT_SYSTEM,
                ToolRackControl,
            )
            context.invoke(
                "tool_rack.change_tool",
                lambda: tool_rack.change_tool(
                    slot,
                    attach=operation == "取",
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            context.log(f"执行换枪出错: {exc}", "error")
            return False

        context.log(
            f"工具架操作完成: slot={slot}, operation={operation}",
            "info",
        )
        return True
