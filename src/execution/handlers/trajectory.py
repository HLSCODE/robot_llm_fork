from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...device_runtime import ArmId, DeviceRuntime, TrajectoryControl
from ...device_runtime.ids import ROBOT_SYSTEM
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionParameters,
    ActionResultCode,
    ActionTimeoutError,
)


@dataclass(frozen=True, slots=True)
class TrajectoryHandlerOptions:
    poll_interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")


class TrajectoryActionHandler:
    """Send a trajectory through the vendor-neutral robot capability."""

    _LOAD_OPERATION = "trajectory.load_file"
    _SEND_OPERATION = "trajectory.send"
    _READ_OPERATION = "trajectory.is_complete"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        options: TrajectoryHandlerOptions,
    ) -> None:
        self._device_runtime = device_runtime
        self._options = options

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        robot_name = str(parameters.get("robot", "robot1")).strip()
        raw_file_path = parameters.get("file_path", "")
        try:
            arm = ArmId.parse(robot_name)
            file_path = Path(raw_file_path)
        except (TypeError, ValueError) as exc:
            message = f"轨迹参数无效: {exc}"
            return context.failure(
                ActionResultCode.INVALID_PARAMETERS,
                message,
                operation=self._SEND_OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        context.log(
            f"执行轨迹动作: robot={robot_name}, file={file_path}",
            "info",
        )
        try:
            is_file = file_path.is_file()
        except OSError as exc:
            message = f"无法访问轨迹文件 {file_path}: {exc}"
            return context.failure(
                ActionResultCode.RESOURCE_NOT_FOUND,
                message,
                operation=self._LOAD_OPERATION,
                device_id=ROBOT_SYSTEM,
            )
        if not is_file:
            message = f"轨迹文件不存在: {file_path}"
            return context.failure(
                ActionResultCode.RESOURCE_NOT_FOUND,
                message,
                operation=self._LOAD_OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        try:
            trajectory = self._device_runtime.require(
                ROBOT_SYSTEM,
                TrajectoryControl,
            )
        except Exception as exc:
            message = f"轨迹设备不可用: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_UNAVAILABLE,
                message,
                operation=self._SEND_OPERATION,
                device_id=ROBOT_SYSTEM,
                error=exc,
            )

        try:
            context.invoke(
                self._SEND_OPERATION,
                lambda: trajectory.send_trajectory(arm, file_path),
            )
            while True:
                complete = context.invoke(
                    self._READ_OPERATION,
                    lambda: trajectory.is_trajectory_complete(arm),
                )
                if complete:
                    context.log("轨迹执行完成", "info")
                    return context.success(
                        operation=self._SEND_OPERATION,
                        device_id=ROBOT_SYSTEM,
                    )
                context.sleep(self._options.poll_interval_seconds)
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            message = f"轨迹执行异常: {exc}"
            return context.failure(
                ActionResultCode.DEVICE_OPERATION_FAILED,
                message,
                operation=self._SEND_OPERATION,
                device_id=ROBOT_SYSTEM,
                error=exc,
            )
