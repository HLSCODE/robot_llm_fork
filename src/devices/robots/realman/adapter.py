from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from ...runtime.arm_models import (
    ArmId,
    ArmState,
    ArmTelemetry,
    CartesianPose,
    GripperTelemetry,
    JointVector,
    MotionMode,
    MotionOptions,
    RobotOperationError,
    TrajectorySaveResult,
)
from ...runtime.models import StopMode
from .state import realman_state_error_codes

_GRAM_FORCE_TO_NEWTONS = 0.00980665


class RealManDriver(Protocol):
    """Vendor-isolating operations required by the project adapter."""

    def stop_arm(self, arm: str, *, emergency: bool) -> int: ...

    def move_to_pose(
        self,
        arm: str,
        pose: list[float],
        *,
        linear: bool,
        velocity: int,
        blend_radius: int,
        connected: bool,
        blocking: bool,
    ) -> int: ...

    def read_state(
        self,
        arm: str,
        *,
        blocking: bool = True,
    ) -> tuple[int, Any] | None: ...

    def read_telemetry(
        self,
        arm: str,
        *,
        blocking: bool = True,
    ) -> dict[str, Any] | None: ...

    def read_motion_diagnostics(self, arm: str) -> dict[str, object]: ...

    def release_gripper(self, arm: str, *, speed: int, timeout_s: int) -> int: ...

    def grip(
        self,
        arm: str,
        *,
        speed: int,
        force: int,
        timeout_s: int,
    ) -> int: ...

    def set_gripper_position(
        self,
        arm: str,
        position: int,
        *,
        timeout_s: int,
    ) -> int: ...

    def follow_joints(
        self,
        arm: str,
        joints: list[float],
        *,
        follow: bool,
        trajectory_mode: int,
    ) -> int: ...

    def initialize_joints(
        self,
        arm: str,
        joints: list[float],
        *,
        velocity: int,
        radius: int,
        connected: bool,
        blocking: bool,
    ) -> int: ...

    def set_drag_teaching(self, arm: str, *, enabled: bool) -> int: ...

    def save_trajectory(self, arm: str, path: str) -> tuple[int, int]: ...

    def send_trajectory(self, arm: str, path: str) -> bool: ...

    def is_trajectory_complete(self, arm: str) -> bool: ...

    def shutdown(self) -> None: ...


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
        controller: RealManDriver,
        *,
        default_motion: MotionOptions,
        tool_rack_options: RealManToolRackOptions,
        gripper_options: RealManGripperOptions | None = None,
    ) -> None:
        self._controller = controller
        self._default_motion = default_motion
        self._gripper_options = gripper_options or RealManGripperOptions()
        self._tool_rack_options = tool_rack_options
        self._stop_lock = RLock()
        self._telemetry_history_lock = RLock()
        self._telemetry_history: dict[
            ArmId,
            tuple[int, tuple[float, ...]],
        ] = {}

    @property
    def supported_stop_modes(self) -> frozenset[StopMode]:
        return frozenset({StopMode.QUICK, StopMode.EMERGENCY})

    def stop(self, mode: StopMode) -> None:
        """Send an interrupting stop to both arms.

        The per-arm SDK lock is intentionally bypassed: a blocking motion call
        may currently own it, and a safety request must still reach the SDK.
        """
        if mode not in self.supported_stop_modes:
            raise ValueError(f"unsupported robot stop mode: {mode}")

        errors: list[str] = []
        with self._stop_lock:
            for arm in ArmId:
                try:
                    code = self._controller.stop_arm(
                        arm.value,
                        emergency=mode is StopMode.EMERGENCY,
                    )
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
        code = self._controller.move_to_pose(
            self._arm_key(arm),
            pose.to_list(),
            linear=mode is MotionMode.LINEAR,
            velocity=selected.velocity_percent,
            blend_radius=selected.blend_radius,
            connected=selected.connected,
            blocking=selected.blocking,
        )
        if code != 0:
            raise RobotOperationError(
                "move_to_pose",
                arm,
                code=int(code),
                detail=self._motion_rejection_detail(arm, pose, mode),
            )

    def read_arm_state(self, arm: ArmId) -> ArmState:
        response = self._controller.read_state(self._arm_key(arm))
        if response is None:
            raise RobotOperationError("read_arm_state", arm, detail="arm is busy")
        code, state = response
        return self._state_from_response(arm, code, state)

    def try_read_arm_state(self, arm: ArmId) -> ArmState | None:
        response = self._controller.read_state(
            self._arm_key(arm),
            blocking=False,
        )
        if response is None:
            return None
        code, state = response
        return self._state_from_response(arm, code, state)

    def read_arm_telemetry(self, arm: ArmId) -> ArmTelemetry:
        payload = self._controller.read_telemetry(self._arm_key(arm))
        if payload is None:
            raise RobotOperationError("read_arm_telemetry", arm, detail="arm is busy")
        return self._telemetry_from_payload(arm, payload)

    def try_read_arm_telemetry(self, arm: ArmId) -> ArmTelemetry | None:
        payload = self._controller.read_telemetry(
            self._arm_key(arm),
            blocking=False,
        )
        if payload is None:
            return None
        return self._telemetry_from_payload(arm, payload)

    def open_gripper(self, arm: ArmId) -> None:
        options = self._gripper_options
        self._retry_sdk_call(
            "open_gripper",
            arm,
            lambda: self._controller.release_gripper(
                self._arm_key(arm),
                speed=options.release_speed,
                timeout_s=options.release_timeout_s,
            ),
        )

    def close_gripper(self, arm: ArmId) -> None:
        options = self._gripper_options
        self._retry_sdk_call(
            "close_gripper",
            arm,
            lambda: self._controller.grip(
                self._arm_key(arm),
                speed=options.pick_speed,
                force=options.pick_force,
                timeout_s=options.pick_timeout_s,
            ),
        )

    def move_gripper(self, arm: ArmId, position: int) -> None:
        if not 0 <= position <= 1000:
            raise ValueError("gripper position must be in range 0..1000")
        options = self._gripper_options
        self._retry_sdk_call(
            "move_gripper",
            arm,
            lambda: self._controller.set_gripper_position(
                self._arm_key(arm),
                position,
                timeout_s=options.release_timeout_s,
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
        code = self._controller.follow_joints(
            self._arm_key(arm),
            joints.to_list(),
            follow=follow,
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
        code = self._controller.initialize_joints(
            self._arm_key(arm),
            joints.to_list(),
            velocity=velocity,
            radius=radius,
            connected=bool(connect),
            blocking=bool(block),
        )
        self._ensure_success("initialize_teleoperation", arm, code)

    def start_drag_teaching(self, arm: ArmId) -> None:
        code = self._controller.set_drag_teaching(
            self._arm_key(arm),
            enabled=True,
        )
        self._ensure_success("start_drag_teaching", arm, code)

    def stop_drag_teaching(self, arm: ArmId) -> None:
        code = self._controller.set_drag_teaching(
            self._arm_key(arm),
            enabled=False,
        )
        self._ensure_success("stop_drag_teaching", arm, code)

    def save_trajectory(
        self,
        arm: ArmId,
        path: str | Path,
    ) -> TrajectorySaveResult:
        normalized_path = Path(path)
        code, point_count = self._controller.save_trajectory(
            self._arm_key(arm),
            str(normalized_path),
        )
        self._ensure_success("save_trajectory", arm, code)
        return TrajectorySaveResult(
            path=normalized_path,
            point_count=int(point_count),
        )

    def send_trajectory(self, arm: ArmId, path: str | Path) -> None:
        if not self._controller.send_trajectory(self._arm_key(arm), str(path)):
            raise RobotOperationError("send_trajectory", arm)

    def is_trajectory_complete(self, arm: ArmId) -> bool:
        return self._controller.is_trajectory_complete(self._arm_key(arm))

    def change_tool(
        self,
        slot: int,
        *,
        attach: bool,
        eject_tool: Callable[[], bool] | None = None,
    ) -> None:
        options = self._tool_rack_options
        slot_config = options.require_slot(slot)
        self._move_tool_rack_pose(options.arm, slot_config.approach_pose)
        if attach:
            self._move_tool_rack_pose(options.arm, slot_config.attach_pose)
            time.sleep(slot_config.attach_dwell_seconds)
        else:
            self._move_tool_rack_pose(options.arm, slot_config.detach_pose)
            if eject_tool is None:
                raise RobotOperationError(
                    "detach_tool",
                    options.arm,
                    detail="tool ejector is not configured",
                )
            try:
                ejected = bool(eject_tool())
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
        self._move_tool_rack_pose(options.arm, slot_config.approach_pose)

    def close(self) -> None:
        self._controller.shutdown()

    @staticmethod
    def _arm_key(arm: ArmId) -> str:
        if not isinstance(arm, ArmId):
            raise TypeError("arm must be an ArmId")
        return arm.value

    def _retry_sdk_call(
        self,
        operation: str,
        arm: ArmId,
        call: Callable[[], int],
    ) -> None:
        last_code: int | None = None
        for attempt in range(self._gripper_options.max_attempts):
            last_code = int(call())
            if last_code == 0:
                return
            if attempt + 1 < self._gripper_options.max_attempts:
                time.sleep(1)
        raise RobotOperationError(operation, arm, code=last_code)

    def _move_tool_rack_pose(
        self,
        arm: ArmId,
        pose: CartesianPose,
    ) -> None:
        options = self._default_motion
        code = self._controller.move_to_pose(
            self._arm_key(arm),
            pose.to_list(),
            linear=True,
            velocity=options.velocity_percent,
            blend_radius=options.blend_radius,
            connected=options.connected,
            blocking=options.blocking,
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
        state_errors = realman_state_error_codes(state)
        if state_errors:
            raise RobotOperationError(
                "read_arm_state",
                arm,
                code=state_errors[0],
                detail=f"device reported errors {state_errors}",
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
            device_error_code=0,
        )

    def _motion_rejection_detail(
        self,
        arm: ArmId,
        target_pose: CartesianPose,
        mode: MotionMode,
    ) -> str:
        details = [
            "controller returned false; check target reachability, joint limits, "
            "emergency-stop/enable state and active robot mode",
            f"mode={mode.value}",
            f"target_pose={target_pose.to_list()}",
        ]
        try:
            response = self._controller.read_state(self._arm_key(arm))
        except Exception as exc:
            details.append(f"state_read_failed={type(exc).__name__}: {exc}")
            return "; ".join(details)
        if response is None:
            details.append("state_read_failed=arm busy")
            return "; ".join(details)
        state_code, state = response
        if state_code != 0 or not isinstance(state, dict):
            details.append(f"state_return_code={state_code}")
            return "; ".join(details)
        state_errors = realman_state_error_codes(state)
        details.append(f"controller_errors={state_errors or 'none reported'}")
        current_pose = state.get("pose")
        if isinstance(current_pose, (list, tuple)):
            details.append(f"current_pose={list(current_pose)}")
        try:
            diagnostics = self._controller.read_motion_diagnostics(
                self._arm_key(arm)
            )
        except Exception as exc:
            details.append(
                f"motion_diagnostics_failed={type(exc).__name__}: {exc}"
            )
        else:
            details.extend(_format_motion_diagnostics(diagnostics))
        return "; ".join(details)

    def _telemetry_from_payload(
        self,
        arm: ArmId,
        payload: dict[str, Any],
    ) -> ArmTelemetry:
        raw_state_response = payload.get("state")
        if not isinstance(raw_state_response, tuple) or len(raw_state_response) != 2:
            raise RobotOperationError(
                "read_arm_telemetry",
                arm,
                detail="driver returned an invalid state response",
            )
        code, raw_state = raw_state_response
        state = self._state_from_response(arm, code, raw_state)
        if state.joints is None:
            raise RobotOperationError(
                "read_arm_telemetry",
                arm,
                detail="SDK state does not contain joint positions",
            )

        gripper = self._read_gripper_telemetry(arm, payload.get("gripper"))
        joint_currents = self._read_optional_joint_currents(
            payload.get("joint_currents"),
            len(state.joints.positions_deg),
        )
        wrench = self._read_optional_end_effector_wrench(payload.get("wrench"))
        sampled_at_monotonic_ns = time.monotonic_ns()
        sampled_at_utc_ns = time.time_ns()
        velocities = self._derive_joint_velocities(
            arm,
            sampled_at_monotonic_ns,
            state.joints.positions_deg,
        )
        return ArmTelemetry(
            state=state,
            sampled_at_utc_ns=sampled_at_utc_ns,
            sampled_at_monotonic_ns=sampled_at_monotonic_ns,
            gripper=gripper,
            joint_velocities_deg_s=velocities,
            joint_currents_amperes=joint_currents,
            end_effector_wrench=wrench,
        )

    @staticmethod
    def _read_gripper_telemetry(
        arm: ArmId,
        response: Any,
    ) -> GripperTelemetry:
        if response is None:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                detail="provider does not expose gripper telemetry",
            )
        if not isinstance(response, tuple) or len(response) != 2:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                detail="driver returned an invalid gripper response",
            )
        code, payload = response
        if code != 0:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                code=int(code),
            )
        if not isinstance(payload, dict):
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                detail="SDK returned an invalid gripper payload",
            )
        if int(payload.get("status", 0)) != 1:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                detail="gripper is offline",
            )
        gripper_error = int(payload.get("error", 0))
        if gripper_error:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                code=gripper_error,
                detail="gripper reported an error",
            )
        raw_position = int(payload.get("actpos", -1))
        if not 0 <= raw_position <= 1000:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                detail=f"invalid gripper position: {raw_position}",
            )
        force_grams = float(payload.get("current_force", 0.0))
        if force_grams < 0:
            raise RobotOperationError(
                "read_gripper_telemetry",
                arm,
                detail=f"invalid gripper force: {force_grams}",
            )
        return GripperTelemetry(
            position_normalized=raw_position / 1000.0,
            force_newtons=force_grams * _GRAM_FORCE_TO_NEWTONS,
            raw_position=raw_position,
        )

    @staticmethod
    def _read_optional_joint_currents(
        response: Any,
        joint_count: int,
    ) -> tuple[float, ...] | None:
        if not isinstance(response, tuple) or len(response) != 2:
            return None
        code, payload = response
        if code != 0:
            return None
        try:
            values = _numeric_tuple(payload, expected_length=joint_count)
        except (TypeError, ValueError):
            return None
        return tuple(value / 1000.0 for value in values)

    @staticmethod
    def _read_optional_end_effector_wrench(
        response: Any,
    ) -> tuple[float, ...] | None:
        if not isinstance(response, tuple) or len(response) != 2:
            return None
        code, payload = response
        if code != 0 or not isinstance(payload, dict):
            return None
        try:
            return _numeric_tuple(payload.get("force_data"), expected_length=6)
        except (TypeError, ValueError):
            return None

    def _derive_joint_velocities(
        self,
        arm: ArmId,
        sampled_at_monotonic_ns: int,
        positions_deg: tuple[float, ...],
    ) -> tuple[float, ...] | None:
        with self._telemetry_history_lock:
            previous = self._telemetry_history.get(arm)
            self._telemetry_history[arm] = (
                sampled_at_monotonic_ns,
                positions_deg,
            )
        if previous is None:
            return None
        previous_ns, previous_positions = previous
        elapsed_seconds = (sampled_at_monotonic_ns - previous_ns) / 1_000_000_000
        if elapsed_seconds <= 0 or len(previous_positions) != len(positions_deg):
            return None
        return tuple(
            (current - prior) / elapsed_seconds
            for current, prior in zip(positions_deg, previous_positions)
        )

    @staticmethod
    def _ensure_success(operation: str, arm: ArmId, code: int) -> None:
        if code != 0:
            raise RobotOperationError(operation, arm, code=int(code))


def _numeric_tuple(
    payload: Any,
    *,
    expected_length: int,
) -> tuple[float, ...]:
    if not isinstance(payload, (list, tuple)) or len(payload) != expected_length:
        raise ValueError(
            f"SDK telemetry must contain {expected_length} numeric values"
        )
    values = tuple(float(value) for value in payload)
    return values


def _format_motion_diagnostics(
    diagnostics: dict[str, object],
) -> tuple[str, ...]:
    formatted: list[str] = []
    run_mode = diagnostics.get("run_mode")
    if isinstance(run_mode, tuple) and len(run_mode) == 2:
        return_code, mode = run_mode
        mode_name = {0: "simulation", 1: "real"}.get(mode, str(mode))
        formatted.append(f"run_mode={mode_name}(code={return_code})")
    joint_enable = diagnostics.get("joint_enable")
    if isinstance(joint_enable, tuple) and len(joint_enable) == 2:
        return_code, states = joint_enable
        formatted.append(f"joint_enable={states}(code={return_code})")
    joint_errors = diagnostics.get("joint_errors")
    if isinstance(joint_errors, dict):
        formatted.append(
            "joint_errors="
            f"{joint_errors.get('err_flag', 'unknown')}"
            f"(code={joint_errors.get('return_code', 'unknown')})"
        )
        formatted.append(
            f"brake_state={joint_errors.get('brake_state', 'unknown')}"
        )
    return tuple(formatted)

