"""Handlers for core actions that do not access a device."""

from __future__ import annotations

from ..handler_api import (
    ActionExecutionContext,
    ActionHandlerResult,
    ActionParameters,
    ActionResultCode,
)


class WaitActionHandler:
    _OPERATION = "wait"

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            wait_seconds = float(parameters.get("wait_seconds", 1.0))
        except (TypeError, ValueError) as exc:
            return context.failure(
                ActionResultCode.INVALID_PARAMETERS,
                f"等待时间无效: {exc}",
                operation=self._OPERATION,
            )
        if wait_seconds <= 0:
            return context.success(operation=self._OPERATION)
        context.log(f"Waiting: {wait_seconds:.1f}s", "info")
        context.sleep(wait_seconds)
        return context.success(operation=self._OPERATION)


class InspectActionHandler:
    """Current simulated inspection handler, isolated for later replacement."""

    _OPERATION = "inspect"

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        sensor_id = parameters.get("Sensor_ID", "")
        threshold = parameters.get("Threshold", 0)
        sensor_timeout = parameters.get("Timeout", 5)
        context.log(
            f"读取传感器 {sensor_id}, 阈值: {threshold}, "
            f"超时: {sensor_timeout}s",
            "info",
        )
        context.sleep(0.8)
        context.log("检测完成 - 结果: 通过", "info")
        return context.success(operation=self._OPERATION)


__all__ = ["InspectActionHandler", "WaitActionHandler"]
