from __future__ import annotations

from dataclasses import dataclass

from ...core.execution_context import ExecutionContext
from ...core.move_compensation import resolve_robot_target_pose
from ...device_runtime import (
    ArmId,
    ArmMotion,
    BodyAxis,
    CartesianPose,
    DeviceRuntime,
    MobileBase,
    MotionMode,
)
from ...device_runtime.ids import BODY_AXIS, MOBILE_BASE, ROBOT_SYSTEM
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionParameters,
    ActionTimeoutError,
)


@dataclass(frozen=True, slots=True)
class MotionHandlerOptions:
    arm_move_max_attempts: int = 3
    arm_move_retry_delay_seconds: float = 0.5
    body_poll_interval_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.arm_move_max_attempts <= 0:
            raise ValueError("arm_move_max_attempts must be positive")
        if self.arm_move_retry_delay_seconds < 0:
            raise ValueError(
                "arm_move_retry_delay_seconds must not be negative"
            )
        if self.body_poll_interval_seconds <= 0:
            raise ValueError(
                "body_poll_interval_seconds must be positive"
            )


class MoveActionHandler:
    """Route MOVE actions to one explicitly supported motion target."""

    def __init__(
        self,
        robot_handler: RobotMoveActionHandler,
        body_handler: BodyMoveActionHandler,
    ) -> None:
        self._target_handlers = {
            "机械臂": robot_handler,
            "身体": body_handler,
        }

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        target = str(parameters.get("目标", "机械臂")).strip()
        handler = self._target_handlers.get(target)
        if handler is None:
            context.log(f"未知的移动目标: {target}", "error")
            return False
        return handler(parameters, context)


class RobotMoveActionHandler:
    """Execute vendor-neutral arm pose movement with bounded retries."""

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        execution_context: ExecutionContext,
        options: MotionHandlerOptions,
    ) -> None:
        self._device_runtime = device_runtime
        self._execution_context = execution_context
        self._options = options

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        arm_name = str(parameters.get("臂", "左"))
        target_pose_text = parameters.get("点位", "")
        mode_name = str(parameters.get("模式", ""))
        context.log(
            f"机械臂移动动作: 臂={arm_name}, 模式={mode_name}, "
            f"点位={target_pose_text}",
            "info",
        )

        try:
            target_pose = resolve_robot_target_pose(
                dict(parameters),
                arm_name,
                self._execution_context,
                lambda message: context.log(message, "info"),
            )
            arm = ArmId.parse(arm_name)
            mode = MotionMode.parse(mode_name)
            pose = CartesianPose.from_iterable(target_pose)
            motion = self._device_runtime.require(
                ROBOT_SYSTEM,
                ArmMotion,
            )
        except Exception as exc:
            context.log(f"机械臂移动参数或设备错误: {exc}", "error")
            return False

        for attempt in range(1, self._options.arm_move_max_attempts + 1):
            try:
                context.invoke(
                    "robot_system.move_to_pose",
                    lambda: motion.move_to_pose(arm, pose, mode),
                )
            except (ActionCancelledError, ActionTimeoutError):
                raise
            except Exception as exc:
                context.log(
                    "机械臂移动失败 "
                    f"(第{attempt}/{self._options.arm_move_max_attempts}次): "
                    f"{exc}",
                    "warn",
                )
            else:
                context.log("机械臂移动执行完成", "info")
                return True

            if attempt < self._options.arm_move_max_attempts:
                context.sleep(
                    self._options.arm_move_retry_delay_seconds
                )

        context.log("机械臂移动重试次数耗尽", "error")
        return False


class BodyMoveActionHandler:
    """Move the body axis and poll completion cooperatively."""

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        options: MotionHandlerOptions,
    ) -> None:
        self._device_runtime = device_runtime
        self._options = options

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        try:
            position = int(parameters.get("位置", 0))
        except (TypeError, ValueError) as exc:
            context.log(f"身体目标位置无效: {exc}", "error")
            return False

        context.log(f"身体移动动作: 目标位置={position}", "info")
        controller = self._device_runtime.get_if_ready(BODY_AXIS)
        if controller is None or not isinstance(controller, BodyAxis):
            context.log("身体控制器未初始化", "error")
            return False

        try:
            context.log(f"正在移动身体到位置 {position}...", "info")
            context.invoke(
                "body_axis.move_to",
                lambda: controller.move_to(position),
            )
            while True:
                reached = context.invoke(
                    "body_axis.is_reached",
                    controller.is_reached,
                )
                if reached is None:
                    context.log("身体通信异常", "error")
                    return False
                if reached:
                    context.log(
                        f"身体移动完成，位置={position}",
                        "info",
                    )
                    return True
                context.sleep(self._options.body_poll_interval_seconds)
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            context.log(f"执行身体移动出错: {exc}", "error")
            return False


class BaseMoveActionHandler:
    """Execute the two supported mobile-base movement modes."""

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        move_mode = str(parameters.get("move_mode", "position")).strip()
        mode_handlers = {
            "position": self._move_to_position,
            "distance": self._move_by_distance,
        }
        handler = mode_handlers.get(move_mode)
        if handler is None:
            context.log(f"未知的移动方式: {move_mode}", "error")
            return False

        controller = self._device_runtime.get_if_ready(MOBILE_BASE)
        if controller is None or not isinstance(controller, MobileBase):
            context.log("底盘移动控制器未初始化", "error")
            return False
        return handler(controller, parameters, context)

    @staticmethod
    def _move_to_position(
        controller: MobileBase,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        try:
            location_id = int(parameters.get("id", 0))
            coordinate_id = int(parameters.get("cid", 0))
        except (TypeError, ValueError) as exc:
            context.log(f"底盘位置参数无效: {exc}", "error")
            return False

        context.log(
            f"底盘位置移动: ID={location_id}, CID={coordinate_id}",
            "info",
        )
        try:
            success = context.invoke(
                "mobile_base.move_to_position",
                lambda: controller.move_to_position(
                    location_id,
                    coordinate_id,
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            context.log(f"执行底盘位置移动出错: {exc}", "error")
            return False

        level = "info" if success else "error"
        context.log(
            f"底盘位置移动{'完成' if success else '失败'}: "
            f"ID={location_id}, CID={coordinate_id}",
            level,
        )
        return success

    @staticmethod
    def _move_by_distance(
        controller: MobileBase,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        try:
            x_cm = float(parameters.get("x", 0.0))
            y_cm = float(parameters.get("y", 0.0))
            angle_degrees = float(parameters.get("angle", 0.0))
        except (TypeError, ValueError) as exc:
            context.log(f"底盘距离参数无效: {exc}", "error")
            return False

        context.log(
            f"底盘距离移动: x={x_cm}cm, y={y_cm}cm, "
            f"angle={angle_degrees}°",
            "info",
        )
        try:
            success = context.invoke(
                "mobile_base.move_slowly",
                lambda: controller.move_slowly(
                    x_cm,
                    y_cm,
                    angle_degrees,
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            context.log(f"执行底盘距离移动出错: {exc}", "error")
            return False

        level = "info" if success else "error"
        context.log(
            f"底盘距离移动{'完成' if success else '失败'}: "
            f"x={x_cm}cm, y={y_cm}cm, angle={angle_degrees}°",
            level,
        )
        return success
