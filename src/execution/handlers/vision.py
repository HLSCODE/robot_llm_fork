from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ...core.execution_context import ExecutionContext
from ...core.settings import VisionSettings
from ...device_runtime import (
    CameraSource,
    DepthCameraSource,
    DeviceRuntime,
    RobotSystem,
)
from ...device_runtime.ids import CAMERA, ROBOT_SYSTEM
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionParameters,
    ActionResultCode,
    ActionTimeoutError,
)


VisionLog = Callable[[str], None]


class VisionCaptureExecutor(Protocol):
    def __call__(
        self,
        robot_system: RobotSystem,
        camera: DepthCameraSource,
        parameters: dict,
        settings: VisionSettings,
        log: VisionLog,
    ) -> bool: ...


class VisionRelocalizationExecutor(Protocol):
    def __call__(
        self,
        robot_system: RobotSystem,
        camera: CameraSource,
        parameters: dict,
        execution_context: ExecutionContext,
        settings: VisionSettings,
        log: VisionLog,
    ) -> bool: ...


class VisionCaptureActionHandler:
    """Run the shared vision-capture flow with runtime-owned devices."""

    _OPERATION = "vision.capture"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        settings: VisionSettings,
        executor: VisionCaptureExecutor | None = None,
    ) -> None:
        self._device_runtime = device_runtime
        self._settings = settings
        self._executor = executor

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            camera = self._device_runtime.require(
                CAMERA,
                DepthCameraSource,
            )
            robot_system = self._device_runtime.require(
                ROBOT_SYSTEM,
                RobotSystem,
            )
        except Exception as exc:
            message = f"视觉抓取设备不可用: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_UNAVAILABLE,
                message,
                operation=self._OPERATION,
                device_id=CAMERA,
                error=exc,
            )

        try:
            result = context.invoke(
                self._OPERATION,
                lambda: self._resolve_executor()(
                    robot_system,
                    camera,
                    dict(parameters),
                    self._settings,
                    lambda message: context.log(message, "info"),
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            message = f"视觉抓取失败: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_OPERATION_FAILED,
                message,
                operation=self._OPERATION,
                device_id=CAMERA,
                error=exc,
            )

        if not result:
            message = "视觉抓取失败"
            return context.failure(
                ActionResultCode.OPERATION_REJECTED,
                message,
                operation=self._OPERATION,
                device_id=CAMERA,
            )
        return context.success(
            operation=self._OPERATION,
            device_id=CAMERA,
        )

    def _resolve_executor(self) -> VisionCaptureExecutor:
        if self._executor is None:
            from ...vision.executor import execute_vision_capture

            self._executor = execute_vision_capture
        return self._executor


class VisionRelocalizationActionHandler:
    """Run vision relocalization with shared domain execution state."""

    _OPERATION = "vision.relocalize"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        execution_context: ExecutionContext,
        settings: VisionSettings,
        executor: VisionRelocalizationExecutor | None = None,
    ) -> None:
        self._device_runtime = device_runtime
        self._execution_context = execution_context
        self._settings = settings
        self._executor = executor

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            robot_system = self._device_runtime.require(
                ROBOT_SYSTEM,
                RobotSystem,
            )
            camera = self._device_runtime.require(CAMERA, CameraSource)
        except Exception as exc:
            message = f"视觉重定位设备不可用: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_UNAVAILABLE,
                message,
                operation=self._OPERATION,
                device_id=CAMERA,
                error=exc,
            )

        try:
            result = context.invoke(
                self._OPERATION,
                lambda: self._resolve_executor()(
                    robot_system,
                    camera,
                    dict(parameters),
                    self._execution_context,
                    self._settings,
                    lambda message: context.log(message, "info"),
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            message = f"视觉重定位失败: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_OPERATION_FAILED,
                message,
                operation=self._OPERATION,
                device_id=CAMERA,
                error=exc,
            )

        if not result:
            message = "视觉重定位失败"
            return context.failure(
                ActionResultCode.OPERATION_REJECTED,
                message,
                operation=self._OPERATION,
                device_id=CAMERA,
            )
        return context.success(
            operation=self._OPERATION,
            device_id=CAMERA,
        )

    def _resolve_executor(self) -> VisionRelocalizationExecutor:
        if self._executor is None:
            from ...vision.relocalization import (
                execute_vision_relocalization,
            )

            self._executor = execute_vision_relocalization
        return self._executor
