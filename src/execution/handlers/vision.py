from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ...devices import (
    CameraSource,
    DepthCameraSource,
    DeviceRuntime,
    GrippingRobotSystem,
    RobotSystem,
)
from ...devices.runtime.ids import CAMERA, ROBOT_SYSTEM
from ...vision.models import VisionResult
from ..handler_api import (
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
        robot_system: GrippingRobotSystem,
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


class CameraRuntimeControl(Protocol):
    def activate_for_execution(
        self,
        camera_names: tuple[str, ...],
        *,
        require_depth: bool,
    ) -> CameraSource: ...

    def defer_idle_shutdown(self) -> None: ...


class VisionCaptureActionHandler:
    """Run the shared vision-capture flow with runtime-owned devices."""

    _OPERATION = "vision.capture"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        vision: VisionOperations,
        camera_runtime: CameraRuntimeControl | None = None,
        camera_name_resolver: Callable[[ActionParameters], str] | None = None,
    ) -> None:
        self._device_runtime = device_runtime
        self._vision = vision
        self._camera_runtime = camera_runtime
        self._camera_name_resolver = camera_name_resolver

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        managed_camera = False
        try:
            if self._camera_runtime is None:
                camera = self._device_runtime.require(CAMERA, DepthCameraSource)
            else:
                camera_name = (
                    self._camera_name_resolver(parameters)
                    if self._camera_name_resolver is not None
                    else ""
                )
                camera = self._camera_runtime.activate_for_execution(
                    (camera_name,) if camera_name else (),
                    require_depth=True,
                )
                if not isinstance(camera, DepthCameraSource):
                    raise TypeError("camera does not provide depth frames")
                managed_camera = True
            robot_system = self._device_runtime.require(
                ROBOT_SYSTEM,
                GrippingRobotSystem,
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
        finally:
            if managed_camera and self._camera_runtime is not None:
                self._camera_runtime.defer_idle_shutdown()


class VisionRelocalizationActionHandler:
    """Run vision relocalization with shared domain execution state."""

    _OPERATION = "vision.relocalize"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        vision: VisionOperations,
        camera_runtime: CameraRuntimeControl | None = None,
        camera_name_resolver: Callable[[ActionParameters], str] | None = None,
    ) -> None:
        self._device_runtime = device_runtime
        self._vision = vision
        self._camera_runtime = camera_runtime
        self._camera_name_resolver = camera_name_resolver

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        managed_camera = False
        try:
            robot_system = self._device_runtime.require(
                ROBOT_SYSTEM,
                RobotSystem,
            )
            if self._camera_runtime is None:
                camera = self._device_runtime.require(CAMERA, CameraSource)
            else:
                camera_name = (
                    self._camera_name_resolver(parameters)
                    if self._camera_name_resolver is not None
                    else ""
                )
                camera = self._camera_runtime.activate_for_execution(
                    (camera_name,) if camera_name else (),
                    require_depth=False,
                )
                managed_camera = True
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
            except ValueError as exc:
                message = f"视觉重定位配置无效: {exc}"
                return context.failure(
                    ActionResultCode.OPERATION_REJECTED,
                    message,
                    operation=self._OPERATION,
                    device_id=CAMERA,
                )
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
        finally:
            if managed_camera and self._camera_runtime is not None:
                self._camera_runtime.defer_idle_shutdown()
