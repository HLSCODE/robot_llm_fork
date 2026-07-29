from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...device_runtime import ArmId, DeviceRuntime, TrajectoryControl
from ...device_runtime.ids import ROBOT_SYSTEM
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionParameters,
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
    ) -> bool:
        robot_name = str(parameters.get("robot", "robot1")).strip()
        raw_file_path = parameters.get("file_path", "")
        try:
            arm = ArmId.parse(robot_name)
            file_path = Path(raw_file_path)
        except (TypeError, ValueError) as exc:
            context.log(f"轨迹参数无效: {exc}", "error")
            return False

        context.log(
            f"执行轨迹动作: robot={robot_name}, file={file_path}",
            "info",
        )
        try:
            is_file = file_path.is_file()
        except OSError as exc:
            context.log(f"无法访问轨迹文件 {file_path}: {exc}", "error")
            return False
        if not is_file:
            context.log(f"轨迹文件不存在: {file_path}", "error")
            return False

        try:
            trajectory = self._device_runtime.require(
                ROBOT_SYSTEM,
                TrajectoryControl,
            )
            context.invoke(
                "trajectory.send",
                lambda: trajectory.send_trajectory(arm, file_path),
            )
            while True:
                complete = context.invoke(
                    "trajectory.is_complete",
                    lambda: trajectory.is_trajectory_complete(arm),
                )
                if complete:
                    context.log("轨迹执行完成", "info")
                    return True
                context.sleep(self._options.poll_interval_seconds)
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            context.log(f"轨迹执行异常: {exc}", "error")
            return False
