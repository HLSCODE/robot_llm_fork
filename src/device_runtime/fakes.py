from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .arm_models import (
    ArmId,
    ArmState,
    ArmTelemetry,
    CartesianPose,
    GripperTelemetry,
    JointVector,
    MotionMode,
    MotionOptions,
    TrajectorySaveResult,
)
from .camera_models import DepthCameraFrame
from .models import StopMode


class SimulatedRobotSystem:
    def __init__(self) -> None:
        self.closed = False
        initial_pose = CartesianPose(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.states = {
            arm: ArmState(
                arm=arm,
                pose=initial_pose,
                joints=JointVector.from_iterable((0.0,) * 7),
            )
            for arm in ArmId
        }
        self.gripper_positions = {arm: 1000 for arm in ArmId}
        self.tool_slot: int | None = None
        self.last_stop_mode: StopMode | None = None

    @property
    def supported_stop_modes(self) -> frozenset[StopMode]:
        return frozenset({StopMode.QUICK, StopMode.EMERGENCY})

    def stop(self, mode: StopMode) -> None:
        if mode not in self.supported_stop_modes:
            raise ValueError(f"unsupported robot stop mode: {mode}")
        self.last_stop_mode = mode

    def move_to_pose(
        self,
        arm: ArmId,
        pose: CartesianPose,
        _mode: MotionMode,
        _options: MotionOptions | None = None,
    ) -> None:
        self.states[arm] = ArmState(
            arm=arm,
            pose=pose,
            joints=self.states[arm].joints,
        )

    def read_arm_state(self, arm: ArmId) -> ArmState:
        return self.states[arm]

    def try_read_arm_state(self, arm: ArmId) -> ArmState:
        return self.read_arm_state(arm)

    def read_arm_telemetry(self, arm: ArmId) -> ArmTelemetry:
        state = self.read_arm_state(arm)
        joint_count = len(state.joints.positions_deg) if state.joints else 0
        return ArmTelemetry(
            state=state,
            sampled_at_utc_ns=time.time_ns(),
            sampled_at_monotonic_ns=time.monotonic_ns(),
            gripper=GripperTelemetry(
                position_normalized=self.gripper_positions[arm] / 1000.0,
                force_newtons=0.0,
                raw_position=self.gripper_positions[arm],
            ),
            joint_velocities_deg_s=(0.0,) * joint_count,
            joint_currents_amperes=(0.0,) * joint_count,
            end_effector_wrench=(0.0,) * 6,
        )

    def try_read_arm_telemetry(self, arm: ArmId) -> ArmTelemetry:
        return self.read_arm_telemetry(arm)

    def open_gripper(self, arm: ArmId) -> None:
        self.gripper_positions[arm] = 1000

    def close_gripper(self, arm: ArmId) -> None:
        self.gripper_positions[arm] = 0

    def move_gripper(self, arm: ArmId, position: int) -> None:
        if not 0 <= position <= 1000:
            raise ValueError("gripper position must be in range 0..1000")
        self.gripper_positions[arm] = position

    def follow_joints(
        self,
        arm: ArmId,
        joints: JointVector,
        *,
        follow: bool,
        trajectory_mode: int,
    ) -> None:
        del follow, trajectory_mode
        state = self.states[arm]
        self.states[arm] = ArmState(
            arm=arm,
            pose=state.pose,
            joints=joints,
        )

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
        del velocity, radius, connect, block
        self.follow_joints(
            arm,
            joints,
            follow=False,
            trajectory_mode=0,
        )

    def start_drag_teaching(self, _arm: ArmId) -> None:
        return None

    def stop_drag_teaching(self, _arm: ArmId) -> None:
        return None

    def save_trajectory(
        self,
        _arm: ArmId,
        path: str | Path,
    ) -> TrajectorySaveResult:
        normalized_path = Path(path)
        normalized_path.write_text("simulated trajectory\n", encoding="utf-8")
        return TrajectorySaveResult(path=normalized_path, point_count=1)

    def send_trajectory(self, _arm: ArmId, path: str | Path) -> None:
        if not Path(path).is_file():
            raise FileNotFoundError(path)

    def is_trajectory_complete(self, _arm: ArmId) -> bool:
        return True

    def change_tool(
        self,
        slot: int,
        *,
        attach: bool,
        eject_tool: Callable[[], bool] | None = None,
    ) -> None:
        if slot not in (1, 2):
            raise ValueError(f"unsupported tool slot: {slot}")
        if not attach and eject_tool is not None and not eject_tool():
            raise RuntimeError("tool ejector reported failure")
        self.tool_slot = slot if attach else None

    def close(self) -> None:
        self.closed = True


class SimulatedBodyAxis:
    def __init__(self) -> None:
        self.position = 0
        self.closed = False

    def move_to(self, position: int) -> None:
        self.position = int(position)

    def is_reached(self) -> bool:
        return True

    def emergency_stop(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class SimulatedMobileBase:
    def __init__(self) -> None:
        self.closed = False

    def move_to_position(self, _location_id: int, _coordinate_id: int) -> bool:
        return True

    def move_slowly(self, _x: float, _y: float, _angle: float) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


class SimulatedNeck:
    def __init__(self) -> None:
        self.horizontal_pwm = 1600
        self.vertical_pwm = 1600
        self.calls: list[tuple[str, int, int | None, int | None]] = []
        self.closed = False

    def move_horizontal(self, pwm: int, time_ms: int | None = None) -> None:
        self.horizontal_pwm = pwm
        self.calls.append(("horizontal", pwm, None, time_ms))

    def move_vertical(self, pwm: int, time_ms: int | None = None) -> None:
        self.vertical_pwm = pwm
        self.calls.append(("vertical", pwm, None, time_ms))

    def move_both(
        self,
        horizontal_pwm: int,
        vertical_pwm: int,
        time_ms: int | None = None,
    ) -> None:
        self.horizontal_pwm = horizontal_pwm
        self.vertical_pwm = vertical_pwm
        self.calls.append(("both", horizontal_pwm, vertical_pwm, time_ms))

    def reset(self, time_ms: int | None = None) -> None:
        self.horizontal_pwm = 1600
        self.vertical_pwm = 1600
        self.calls.append(("reset", 1600, 1600, time_ms))

    def close(self) -> None:
        self.closed = True


class SimulatedDigitalOutputs:
    def __init__(self) -> None:
        self.channels: dict[int, bool] = {}

    def set_channel(self, channel: int, enabled: bool) -> None:
        self.channels[channel] = enabled

    def close(self) -> None:
        return None

    def enter_safe_state(self) -> None:
        self.channels.update({1: False, 2: False})


class SimulatedToolChanger:
    def __init__(self) -> None:
        self.locked = False

    def set_locked(self, locked: bool) -> None:
        self.locked = locked

    def close(self) -> None:
        return None

    def enter_safe_state(self) -> None:
        self.locked = True


class SimulatedPipette:
    def __init__(self) -> None:
        self.safe = False

    def initialize(self) -> bool:
        return True

    def set_absorb_speed(self, _speed_ul_s: int) -> bool:
        return True

    def set_dispense_speed(self, _speed_ul_s: int) -> bool:
        return True

    def absorb(self, _volume_ul: int) -> bool:
        return True

    def dispense(self, _volume_ul: int) -> bool:
        return True

    def dispense_all(self) -> bool:
        return True

    def eject_tip(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def enter_safe_state(self) -> None:
        self.safe = True


class SimulatedPowderDispenser:
    def enable_all(self) -> None:
        return None

    def gripper_move_to(self, _percent: int) -> None:
        return None

    def gripper_grip(self) -> None:
        return None

    def gripper_release(self) -> None:
        return None

    def lift_up(self, _steps: int) -> None:
        return None

    def lift_down(self, _steps: int) -> None:
        return None

    def lift_stop(self) -> None:
        return None

    def lift_to_dispense(self, _position: int) -> None:
        return None

    def lift_to_safe(self, _position: int) -> None:
        return None

    def rotation_cw(self, _steps: int) -> None:
        return None

    def rotation_ccw(self, _steps: int) -> None:
        return None

    def rotation_stop(self) -> None:
        return None

    def rotation_move_relative(self, _delta_steps: int) -> None:
        return None

    def rotation_to_home(self, _position: int) -> None:
        return None

    def close(self) -> None:
        return None


class SimulatedCamera:
    camera_count = 1
    is_running = True

    def start(self) -> dict[str, int]:
        self.is_running = True
        return {"started": 1, "failed": 0}

    def stop(self) -> None:
        self.is_running = False

    def get_cameras_info(self) -> list[dict[str, Any]]:
        return [{"name": "simulation", "online": self.is_running}]

    def get_latest_jpegs(self) -> list[tuple[str, str, bytes]]:
        return []

    def get_latest_raw_frames(self, _camera_name: str | None = None) -> None:
        return None

    def get_latest_depth_frame(
        self,
        _camera_name: str | None = None,
    ) -> DepthCameraFrame | None:
        return None


class SimulatedExpressionDisplay:
    def __init__(self) -> None:
        self.expression: str | int | None = None

    def switch(self, expression: str | int) -> str | int:
        self.expression = expression
        return expression

    def close(self) -> None:
        return None
