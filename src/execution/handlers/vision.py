from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ...device_runtime import (
    CameraSource,
    DepthCameraSource,
    DeviceRuntime,
    RobotSystem,
)
from ...device_runtime.ids import CAMERA, ROBOT_SYSTEM
from ...vision.models import VisionResult
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionParameters,
    ActionResultCode,
    ActionTimeoutError,
)


class VisionOperations(Protocol):
    def capture(
        self,
        robot_system: RobotSystem,
        camera: DepthCameraSource,
        parameters: dict[str, object],
        log: Callable[[str], None],
    ) -> VisionResult: ...

    def relocalize(
        self,
        robot_system: RobotSystem,
        camera: CameraSource,
        parameters: dict[str, object],
        log: Callable[[str], None],
    ) -> VisionResult: ...


class VisionCaptureActionHandler:
    """Run the shared vision-capture flow with runtime-owned devices."""

    _OPERATION = "vision.capture"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        vision: VisionOperations,
    ) -> None:
        self._device_runtime = device_runtime
        self._vision = vision

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
            result: VisionResult = context.invoke(
                self._OPERATION,
                lambda: self._vision.capture(
                    robot_system,
                    camera,
                    dict(parameters),
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

        if not result.successful:
            return context.failure(
                ActionResultCode.OPERATION_REJECTED,
                result.message,
                operation=self._OPERATION,
                device_id=CAMERA,
            )
        return context.success(
            operation=self._OPERATION,
            device_id=CAMERA,
        )

class VisionRelocalizationActionHandler:
    """Run vision relocalization with shared domain execution state."""

    _OPERATION = "vision.relocalize"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        vision: VisionOperations,
    ) -> None:
        self._device_runtime = device_runtime
        self._vision = vision

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
            result: VisionResult = context.invoke(
                self._OPERATION,
                lambda: self._vision.relocalize(
                    robot_system,
                    camera,
                    dict(parameters),
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

        if not result.successful:
            return context.failure(
                ActionResultCode.OPERATION_REJECTED,
                result.message,
                operation=self._OPERATION,
                device_id=CAMERA,
            )
        return context.success(
            operation=self._OPERATION,
            device_id=CAMERA,
        )
