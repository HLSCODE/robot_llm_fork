from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .arm_models import (
    ArmId,
    ArmState,
    CartesianPose,
    JointVector,
    MotionMode,
    MotionOptions,
    RobotOperationError,
    TrajectorySaveResult,
)
from .models import StopMode


@dataclass(frozen=True, slots=True)
class RealManGripperOptions:
    pick_speed: int = 200
    pick_force: int = 1000
    pick_timeout_s: int = 3
    release_speed: int = 100
    release_timeout_s: int = 3
    max_attempts: int = 5

    def __post_init__(self) -> None:
        values = (
            self.pick_speed,
            self.pick_force,
            self.pick_timeout_s,
            self.release_speed,
            self.release_timeout_s,
        )
        if any(value <= 0 for value in values):
            raise ValueError("RealMan gripper options must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True, slots=True)
class RealManToolRackSlot:
    slot_id: int
    approach_pose: CartesianPose
    attach_pose: CartesianPose
    detach_pose: CartesianPose
    attach_dwell_seconds: float = 0.5
    detach_dwell_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.slot_id <= 0:
            raise ValueError("tool rack slot_id must be positive")
        if self.attach_dwell_seconds < 0:
            raise ValueError(
                "tool rack attach_dwell_seconds must not be negative"
            )
        if self.detach_dwell_seconds < 0:
            raise ValueError(
                "tool rack detach_dwell_seconds must not be negative"
            )


@dataclass(frozen=True, slots=True)
class RealManToolRackOptions:
    arm: ArmId
    slots: tuple[RealManToolRackSlot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.arm, ArmId):
            raise TypeError("tool rack arm must be an ArmId")
        if not self.slots:
            raise ValueError("at least one tool rack slot is required")
        slot_ids = tuple(slot.slot_id for slot in self.slots)
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError("tool rack slot ids must be unique")

    def require_slot(self, slot_id: int) -> RealManToolRackSlot:
        for slot in self.slots:
            if slot.slot_id == slot_id:
                return slot
        raise ValueError(f"unsupported tool slot: {slot_id}")


class RealManRobotAdapter:
    """Translate the RealMan SDK/controller shape into project capabilities."""

    def __init__(
        self,
        controller: Any,
        *,
        default_motion: MotionOptions,
        tool_rack_options: RealManToolRackOptions,
        gripper_options: RealManGripperOptions | None = None,
        eject_tool: Callable[[], bool] | None = None,
    ) -> None:
        self._controller = controller
        self._default_motion = default_motion
        self._gripper_options = gripper_options or RealManGripperOptions()
        self._tool_rack_options = tool_rack_options
        self._eject_tool = eject_tool
        self._stop_lock = RLock()

    @property
    def supported_stop_modes(self) -> frozenset[StopMode]:
        return frozenset({StopMode.QUICK, StopMode.EMERGENCY})

    def stop(self, mode: StopMode) -> None:
        """Send an interrupting stop to both arms.

        The per-arm SDK lock is intentionally bypassed: a blocking motion call
        may currently own it, and a safety request must still reach the SDK.
        """
        methods = {
            StopMode.QUICK: "rm_set_arm_slow_stop",
            StopMode.EMERGENCY: "rm_set_arm_stop",
        }
        try:
            method_name = methods[mode]
        except KeyError as exc:
            raise ValueError(f"unsupported robot stop mode: {mode}") from exc

        errors: list[str] = []
        with self._stop_lock:
            for arm in ArmId:
                try:
                    _arm_controller, robot = self._arm_backend(arm)
                    code = int(getattr(robot, method_name)())
                    if code != 0:
                        errors.append(f"{arm.value}: SDK code {code}")
                except Exception as exc:
                    errors.append(f"{arm.value}: {exc}")
        if errors:
            raise RuntimeError(
                f"{mode.value} stop failed for " + "; ".join(errors)
            )

    def move_to_pose(
        self,
        arm: ArmId,
        pose: CartesianPose,
        mode: MotionMode,
        options: MotionOptions | None = None,
    ) -> None:
        if not isinstance(mode, MotionMode):
            raise TypeError("mode must be a MotionMode")
        selected = options or self._default_motion
        arm_controller, robot = self._arm_backend(arm)
        kwargs = {
            "v": selected.velocity_percent,
            "r": selected.blend_radius,
            "connect": int(selected.connected),
            "block": int(selected.blocking),
        }
        with arm_controller.sdk_lock:
            if mode is MotionMode.JOINT:
                code = robot.rm_movej_p(pose.to_list(), **kwargs)
            else:
                code = robot.rm_movel(pose.to_list(), **kwargs)
        self._ensure_success("move_to_pose", arm, code)

    def read_arm_state(self, arm: ArmId) -> ArmState:
        arm_controller, robot = self._arm_backend(arm)
        with arm_controller.sdk_lock:
            code, state = robot.rm_get_current_arm_state()
        return self._state_from_response(arm, code, state)

    def try_read_arm_state(self, arm: ArmId) -> ArmState | None:
        arm_controller, robot = self._arm_backend(arm)
        if not arm_controller.sdk_lock.acquire(blocking=False):
            return None
        try:
            code, state = robot.rm_get_current_arm_state()
        finally:
            arm_controller.sdk_lock.release()
        return self._state_from_response(arm, code, state)

    def open_gripper(self, arm: ArmId) -> None:
        options = self._gripper_options
        self._retry_sdk_call(
            "open_gripper",
            arm,
            lambda robot: robot.rm_set_gripper_release(
                speed=options.release_speed,
                block=True,
                timeout=options.release_timeout_s,
            ),
        )

    def close_gripper(self, arm: ArmId) -> None:
        options = self._gripper_options
        self._retry_sdk_call(
            "close_gripper",
            arm,
            lambda robot: robot.rm_set_gripper_pick_on(
                speed=options.pick_speed,
                block=True,
                timeout=options.pick_timeout_s,
                force=options.pick_force,
            ),
        )

    def move_gripper(self, arm: ArmId, position: int) -> None:
        if not 0 <= position <= 1000:
            raise ValueError("gripper position must be in range 0..1000")
        options = self._gripper_options
        self._retry_sdk_call(
            "move_gripper",
            arm,
            lambda robot: robot.rm_set_gripper_position(
                position,
                block=True,
                timeout=options.release_timeout_s,
            ),
        )

    def follow_joints(
        self,
        arm: ArmId,
        joints: JointVector,
        *,
        follow: bool,
        trajectory_mode: int,
    ) -> None:
        arm_controller, robot = self._arm_backend(arm)
        with arm_controller.sdk_lock:
            code = robot.rm_movej_canfd(
                joints.to_list(),
                follow,
                trajectory_mode=trajectory_mode,
            )
        self._ensure_success("follow_joints", arm, code)

    def initialize_teleoperation(
        self,
        arm: ArmId,
        joints: JointVector,
        *,
        velocity: int = 10,
        radius: int = 0,
        connect: int = 0,
        block: int = 1,
    ) -> None:
        arm_controller, robot = self._arm_backend(arm)
        with arm_controller.sdk_lock:
            code = robot.rm_movej(
                joints.to_list(),
                velocity,
                radius,
                connect,
                block,
            )
        self._ensure_success("initialize_teleoperation", arm, code)

    def start_drag_teaching(self, arm: ArmId) -> None:
        arm_controller, robot = self._arm_backend(arm)
        with arm_controller.sdk_lock:
            code = robot.rm_start_drag_teach(1)
        self._ensure_success("start_drag_teaching", arm, code)

    def stop_drag_teaching(self, arm: ArmId) -> None:
        arm_controller, robot = self._arm_backend(arm)
        with arm_controller.sdk_lock:
            code = robot.rm_stop_drag_teach()
        self._ensure_success("stop_drag_teaching", arm, code)

    def save_trajectory(
        self,
        arm: ArmId,
        path: str | Path,
    ) -> TrajectorySaveResult:
        normalized_path = Path(path)
        arm_controller, robot = self._arm_backend(arm)
        with arm_controller.sdk_lock:
            code, point_count = robot.rm_save_trajectory(str(normalized_path))
        self._ensure_success("save_trajectory", arm, code)
        return TrajectorySaveResult(
            path=normalized_path,
            point_count=int(point_count),
        )

    def send_trajectory(self, arm: ArmId, path: str | Path) -> None:
        _arm_controller, robot = self._arm_backend(arm)
        if not self._controller.demo_send_project(
            robot,
            str(path),
            project_type=1,
        ):
            raise RobotOperationError("send_trajectory", arm)

    def is_trajectory_complete(self, arm: ArmId) -> bool:
        _arm_controller, robot = self._arm_backend(arm)
        return bool(
            self._controller.demo_get_program_run_state(
                robot,
                time_sleep=0,
                max_retries=1,
            )
        )

    def change_tool(self, slot: int, *, attach: bool) -> None:
        options = self._tool_rack_options
        slot_config = options.require_slot(slot)
        arm_controller, robot = self._arm_backend(options.arm)
        with arm_controller.sdk_lock:
            self._move_tool_rack_pose(
                robot,
                options.arm,
                slot_config.approach_pose,
            )
            if attach:
                self._move_tool_rack_pose(
                    robot,
                    options.arm,
                    slot_config.attach_pose,
                )
                time.sleep(slot_config.attach_dwell_seconds)
            else:
                self._move_tool_rack_pose(
                    robot,
                    options.arm,
                    slot_config.detach_pose,
                )
                if self._eject_tool is None:
                    raise RobotOperationError(
                        "detach_tool",
                        options.arm,
                        detail="tool ejector is not configured",
                    )
                try:
                    ejected = bool(self._eject_tool())
                except Exception as exc:
                    raise RobotOperationError(
                        "detach_tool",
                        options.arm,
                        detail=f"tool ejector failed: {exc}",
                    ) from exc
                if not ejected:
                    raise RobotOperationError(
                        "detach_tool",
                        options.arm,
                        detail="tool ejector reported failure",
                    )
                time.sleep(slot_config.detach_dwell_seconds)
            self._move_tool_rack_pose(
                robot,
                options.arm,
                slot_config.approach_pose,
            )

    def close(self) -> None:
        self._controller.shutdown()

    def _arm_backend(self, arm: ArmId) -> tuple[Any, Any]:
        if not isinstance(arm, ArmId):
            raise TypeError("arm must be an ArmId")
        arm_controller = (
            self._controller.robot1_ctrl
            if arm is ArmId.LEFT
            else self._controller.robot2_ctrl
        )
        robot = getattr(arm_controller, "robot", None)
        if robot is None or not getattr(arm_controller, "is_connected", False):
            raise RobotOperationError(
                "resolve_arm",
                arm,
                detail="arm is not connected",
            )
        return arm_controller, robot

    def _retry_sdk_call(
        self,
        operation: str,
        arm: ArmId,
        call: Callable[[Any], int],
    ) -> None:
        arm_controller, robot = self._arm_backend(arm)
        last_code: int | None = None
        for attempt in range(self._gripper_options.max_attempts):
            with arm_controller.sdk_lock:
                last_code = int(call(robot))
            if last_code == 0:
                return
            if attempt + 1 < self._gripper_options.max_attempts:
                time.sleep(1)
        raise RobotOperationError(operation, arm, code=last_code)

    def _move_tool_rack_pose(
        self,
        robot: Any,
        arm: ArmId,
        pose: CartesianPose,
    ) -> None:
        options = self._default_motion
        code = robot.rm_movel(
            pose.to_list(),
            v=options.velocity_percent,
            r=options.blend_radius,
            connect=int(options.connected),
            block=int(options.blocking),
        )
        self._ensure_success("tool_rack.move", arm, code)

    @staticmethod
    def _state_from_response(
        arm: ArmId,
        code: int,
        state: Any,
    ) -> ArmState:
        if code != 0:
            raise RobotOperationError("read_arm_state", arm, code=code)
        if not isinstance(state, dict):
            raise RobotOperationError(
                "read_arm_state",
                arm,
                detail="SDK returned an invalid state payload",
            )
        device_error = int(state.get("error_code", 0))
        if device_error:
            raise RobotOperationError(
                "read_arm_state",
                arm,
                code=device_error,
                detail="device reported an error",
            )
        pose = CartesianPose.from_iterable(state.get("pose", ()))
        raw_joints = state.get("joint")
        joints = (
            JointVector.from_iterable(raw_joints)
            if isinstance(raw_joints, (list, tuple)) and raw_joints
            else None
        )
        return ArmState(
            arm=arm,
            pose=pose,
            joints=joints,
            device_error_code=device_error,
        )

    @staticmethod
    def _ensure_success(operation: str, arm: ArmId, code: int) -> None:
        if code != 0:
            raise RobotOperationError(operation, arm, code=int(code))

class RelayBankAdapter:
    """Expose numbered digital outputs without leaking relay method names."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    def set_channel(self, channel: int, enabled: bool) -> None:
        if channel not in (1, 2):
            raise ValueError(f"unsupported relay channel: {channel}")
        state = "on" if enabled else "off"
        method = getattr(self._controller, f"turn_{state}_relay_Y{channel}")
        method()

    def close(self) -> None:
        self._controller.close()


class ToolChangerAdapter:
    """Expose lock state instead of serial command strings."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    def set_locked(self, locked: bool) -> None:
        command = "close" if locked else "open"
        result = self._controller.send_command(command)
        if result == "error" or result is False:
            raise RuntimeError(f"tool changer command failed: {result}")

    def close(self) -> None:
        self._controller.close()


class PipetteAdapter:
    """Combine the ADP liquid controller and tip operations."""

    def __init__(
        self,
        controller: Any,
        *,
        tip_port: str,
        initialize_tip: Callable[..., bool],
        eject_tip: Callable[..., bool],
    ) -> None:
        self._controller = controller
        self._tip_port = tip_port
        self._initialize_tip = initialize_tip
        self._eject_tip = eject_tip

    def initialize(self) -> bool:
        return bool(self._initialize_tip(port=self._tip_port))

    def set_absorb_speed(self, speed_ul_s: int) -> bool:
        return bool(self._controller.set_absorb_speed(speed_ul_s))

    def set_dispense_speed(self, speed_ul_s: int) -> bool:
        return bool(self._controller.set_dispense_speed(speed_ul_s))

    def absorb(self, volume_ul: int) -> bool:
        return bool(self._controller.absorb(volume_ul))

    def dispense(self, volume_ul: int) -> bool:
        return bool(self._controller.dispense(volume_ul))

    def dispense_all(self) -> bool:
        return bool(self._controller.dispense_all())

    def eject_tip(self) -> bool:
        return bool(self._eject_tip(port=self._tip_port))

    def close(self) -> None:
        self._controller.close()
