from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


class RobotSdk(Protocol):
    def connect(self, robot_ip: str) -> object: ...
    def subscribe(self, state_buffer: object) -> dict[str, Any] | None: ...
    def release_robot(self) -> object: ...
    def clear_set(self) -> object: ...
    def clear_error(self, arm: str) -> object: ...
    def set_state(self, arm: str, state: int) -> object: ...
    def set_vel_acc(self, arm: str, velRatio: int, AccRatio: int) -> object: ...
    def send_cmd(self) -> object: ...
    def set_joint_cmd_pose(self, arm: str, joints: list[float]) -> object: ...
    def setPln_Cart(self, arm: str, pset: object) -> object: ...
    def soft_stop(self, arm: str) -> object: ...


class KinematicsSdk(Protocol):
    def load_config(self, arm_type: int, config_path: str) -> object: ...
    def initial_kine(
        self,
        robot_type: int,
        dh: list[object],
        pnva: list[object],
        j67: list[object],
    ) -> object: ...
    def fk(self, joints: list[float]) -> object: ...
    def mat4x4_to_xyzabc(self, pose_mat: list[list[float]]) -> object: ...
    def xyzabc_to_mat4x4(self, xyzabc: list[float]) -> object: ...
    def ik(self, structure_data: IKParameters) -> IKParameters | None: ...
    def movLA(
        self,
        start_xyzabc: list[float],
        end_xyzabc: list[float],
        ref_joints: list[float],
        vel: float,
        acc: float,
        freq_hz: int,
    ) -> tuple[list[list[float]], object | None]: ...
    def destroy_point_set(self, pset: object) -> None: ...


class IKParameters(Protocol):
    def set_input_ik_target_tcp(self, matrix: list[float]) -> None: ...
    def set_input_ik_ref_joint(self, values: list[float]) -> None: ...
    def set_input_ik_zsp_type(self, value: int) -> None: ...
    def get_output_ret_joint(self) -> list[float]: ...


class TianjiRobotDriver:
    """Own one Tianji SDK connection and both arm kinematics contexts."""

    def __init__(
        self,
        controller_ip: str,
        *,
        kinematics_config: str,
        acceleration_percent: int,
        linear_acceleration_m_s2: float,
        robot: RobotSdk | None = None,
        state_buffer: object | None = None,
        kinematics: dict[str, KinematicsSdk] | None = None,
        ik_parameter_factory: Callable[[], IKParameters] | None = None,
    ) -> None:
        if not controller_ip.strip():
            raise ValueError("Tianji controller IP must not be empty")
        if not 1 <= acceleration_percent <= 100:
            raise ValueError("Tianji acceleration percent must be in range 1..100")
        if not 0.0001 <= linear_acceleration_m_s2 <= 0.5:
            raise ValueError(
                "Tianji linear acceleration must be in range 0.0001..0.5 m/s²"
            )
        self._lock = RLock()
        self._closed = False
        self._connected = False
        self._active_point_sets: dict[str, tuple[KinematicsSdk, object]] = {}
        self._acceleration_percent = acceleration_percent
        self._linear_acceleration_mm_s2 = linear_acceleration_m_s2 * 1000.0

        if robot is None:
            robot, state_buffer, kinematics, ik_parameter_factory = _load_sdk(
                kinematics_config
            )
        if state_buffer is None or kinematics is None or ik_parameter_factory is None:
            raise ValueError("incomplete Tianji SDK dependencies")
        self._robot = robot
        self._state_buffer = state_buffer
        self._kinematics = kinematics
        self._ik_parameter_factory = ik_parameter_factory

        try:
            if not bool(self._robot.connect(controller_ip)):
                raise RuntimeError("SDK rejected controller connection")
            self._connected = True
            self._initialize_arms()
            self._require_snapshot()
        except Exception:
            self.close()
            raise

    def move_to_pose(
        self,
        arm: str,
        pose: list[float],
        *,
        linear: bool,
        velocity_percent: int,
        blocking: bool,
    ) -> bool:
        if len(pose) != 6:
            raise ValueError("Tianji pose must contain 6 values")
        if not 1 <= velocity_percent <= 100:
            raise ValueError("velocity_percent must be in range 1..100")
        sdk_arm = _validate_arm(arm)
        with self._lock:
            snapshot = self._require_snapshot()
            arm_payload = _arm_output(snapshot, sdk_arm)
            joints = _numeric_list(arm_payload.get("fb_joint_pos"), 7, "joints")
            kine = self._kinematics[sdk_arm]
            target_xyzabc = _pose_to_sdk_xyzabc(pose)
            if linear:
                return self._move_linear(
                    sdk_arm,
                    kine,
                    joints,
                    target_xyzabc,
                    velocity_percent,
                    blocking,
                )
            return self._move_joint_path(
                sdk_arm,
                kine,
                joints,
                target_xyzabc,
                velocity_percent,
                blocking,
            )

    def read_state(self, arm: str) -> dict[str, object]:
        sdk_arm = _validate_arm(arm)
        with self._lock:
            snapshot = self._require_snapshot()
            output = _arm_output(snapshot, sdk_arm)
            joints = _numeric_list(output.get("fb_joint_pos"), 7, "joints")
            matrix = self._kinematics[sdk_arm].fk(joints)
            if not isinstance(matrix, list):
                raise RuntimeError("Tianji forward kinematics failed")
            xyzabc = self._kinematics[sdk_arm].mat4x4_to_xyzabc(matrix)
            sdk_pose = _numeric_list(xyzabc, 6, "pose")
            states = snapshot.get("states")
            state = _indexed_mapping(states, 0 if sdk_arm == "A" else 1, "states")
            return {
                "pose": _pose_from_sdk_xyzabc(sdk_pose),
                "joints": joints,
                "error_code": int(state.get("err_code", 0)),
            }

    def stop_arm(self, arm: str, *, emergency: bool) -> bool:
        del emergency  # SDK exposes one interrupting software-stop primitive.
        sdk_arm = _validate_arm(arm)
        return bool(self._robot.soft_stop(sdk_arm))

    def close(self) -> None:
        if self._connected:
            try:
                self._robot.soft_stop("AB")
            except Exception:
                pass
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for arm in tuple(self._active_point_sets):
                self._release_active_point_set(arm)
            try:
                self._robot.release_robot()
            except Exception:
                pass
            self._connected = False

    def _initialize_arms(self) -> None:
        with self._lock:
            self._robot.clear_set()
            for arm in ("A", "B"):
                self._robot.clear_error(arm)
                self._robot.set_state(arm, 1)
                self._robot.set_vel_acc(
                    arm,
                    self._acceleration_percent,
                    self._acceleration_percent,
                )
            if not bool(self._robot.send_cmd()):
                raise RuntimeError("Tianji initialization command failed")

    def _require_snapshot(self) -> dict[str, Any]:
        snapshot = self._robot.subscribe(self._state_buffer)
        if not isinstance(snapshot, dict):
            raise RuntimeError("Tianji state subscription failed")
        return snapshot

    def _move_linear(
        self,
        arm: str,
        kine: KinematicsSdk,
        joints: list[float],
        target_xyzabc: list[float],
        velocity_percent: int,
        blocking: bool,
    ) -> bool:
        self._release_active_point_set(arm)
        start_matrix = kine.fk(joints)
        if not isinstance(start_matrix, list):
            return False
        start_pose = kine.mat4x4_to_xyzabc(start_matrix)
        start_xyzabc = _numeric_list(start_pose, 6, "start pose")
        _, point_set = kine.movLA(
            start_xyzabc,
            target_xyzabc,
            joints,
            max(0.1, velocity_percent / 100.0 * 500.0),
            self._linear_acceleration_mm_s2,
            500,
        )
        if point_set is None:
            return False
        succeeded = bool(self._robot.setPln_Cart(arm, point_set))
        if not succeeded:
            kine.destroy_point_set(point_set)
            return False
        if not blocking:
            self._active_point_sets[arm] = (kine, point_set)
            return True
        try:
            self._wait_until_stopped(arm)
            return True
        finally:
            kine.destroy_point_set(point_set)

    def _move_joint_path(
        self,
        arm: str,
        kine: KinematicsSdk,
        joints: list[float],
        target_xyzabc: list[float],
        velocity_percent: int,
        blocking: bool,
    ) -> bool:
        target_matrix = kine.xyzabc_to_mat4x4(target_xyzabc)
        if not isinstance(target_matrix, list):
            return False
        parameters = self._ik_parameter_factory()
        parameters.set_input_ik_target_tcp(_flatten_matrix(target_matrix))
        parameters.set_input_ik_ref_joint(joints)
        parameters.set_input_ik_zsp_type(0)
        result = kine.ik(parameters)
        if not result:
            return False
        target_joints = _numeric_list(result.get_output_ret_joint(), 7, "IK joints")
        self._robot.clear_set()
        self._robot.set_vel_acc(
            arm,
            velocity_percent,
            self._acceleration_percent,
        )
        accepted = bool(self._robot.set_joint_cmd_pose(arm, target_joints))
        succeeded = accepted and bool(self._robot.send_cmd())
        if succeeded and blocking:
            self._wait_until_stopped(arm)
        return succeeded

    def _wait_until_stopped(self, arm: str, timeout_seconds: float = 60.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        output_index = 0 if arm == "A" else 1
        consecutive_idle_samples = 0
        time.sleep(0.02)
        while time.monotonic() < deadline:
            snapshot = self._require_snapshot()
            output = _indexed_mapping(snapshot.get("outputs"), output_index, "outputs")
            velocities = _numeric_list(output.get("fb_joint_vel"), 7, "velocities")
            if max(abs(value) for value in velocities) <= 0.01:
                consecutive_idle_samples += 1
                if consecutive_idle_samples >= 3:
                    return
            else:
                consecutive_idle_samples = 0
            time.sleep(0.01)
        raise TimeoutError(f"Tianji arm {arm} motion did not stop within timeout")

    def _release_active_point_set(self, arm: str) -> None:
        active = self._active_point_sets.pop(arm, None)
        if active is None:
            return
        kinematics, point_set = active
        kinematics.destroy_point_set(point_set)


def _load_sdk(
    config_name: str,
) -> tuple[
    RobotSdk,
    object,
    dict[str, KinematicsSdk],
    Callable[[], IKParameters],
]:
    try:
        from tj_robot_proj.app.tj_arms_app.SDK_PYTHON.fx_kine import (
            FX_InvKineSolvePara,
            Marvin_Kine,
        )
        from tj_robot_proj.app.tj_arms_app.SDK_PYTHON.fx_robot import (
            DCSS,
            Marvin_Robot,
        )
        import tj_robot_proj.app.tj_arms_app.SDK_PYTHON.fx_kine as kine_module
    except ImportError as exc:
        raise RuntimeError(
            "Tianji SDK unavailable; install the platform-specific tj-robot-proj wheel"
        ) from exc

    if Path(config_name).name != config_name:
        raise RuntimeError("Tianji kinematics config must be a packaged file name")
    config_path = Path(kine_module.__file__).resolve().parent / config_name
    if not config_path.is_file():
        raise RuntimeError(f"Tianji kinematics config not found: {config_name}")
    kinematics: dict[str, KinematicsSdk] = {
        "A": Marvin_Kine(),
        "B": Marvin_Kine(),
    }
    for arm, arm_type in (("A", 0), ("B", 1)):
        loaded = kinematics[arm].load_config(arm_type, str(config_path))
        if not isinstance(loaded, dict):
            raise RuntimeError(f"failed to initialize Tianji {arm} arm kinematics")
        try:
            initialized = kinematics[arm].initial_kine(
                robot_type=int(loaded["TYPE"][arm_type]),
                dh=loaded["DH"][arm_type],
                pnva=loaded["PNVA"][arm_type],
                j67=loaded["BD"][arm_type],
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"invalid Tianji {arm} arm kinematics configuration"
            ) from exc
        if not initialized:
            raise RuntimeError(f"failed to activate Tianji {arm} arm kinematics")
    return Marvin_Robot(), DCSS(), kinematics, FX_InvKineSolvePara


def _validate_arm(arm: str) -> str:
    normalized = str(arm).strip().upper()
    if normalized not in {"A", "B"}:
        raise ValueError(f"Tianji arm must be A or B, got {arm!r}")
    return normalized


def _arm_output(snapshot: dict[str, Any], arm: str) -> dict[str, Any]:
    return _indexed_mapping(snapshot.get("outputs"), 0 if arm == "A" else 1, "outputs")


def _indexed_mapping(value: object, index: int, label: str) -> dict[str, Any]:
    if not isinstance(value, (list, tuple)) or len(value) <= index:
        raise RuntimeError(f"Tianji snapshot has invalid {label}")
    item = value[index]
    if not isinstance(item, dict):
        raise RuntimeError(f"Tianji snapshot has invalid {label}[{index}]")
    return item


def _numeric_list(value: object, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise RuntimeError(f"Tianji {label} must contain {length} values")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise RuntimeError(f"Tianji {label} contains non-finite values")
    return result


def _pose_to_sdk_xyzabc(pose: list[float]) -> list[float]:
    return [
        pose[0] * 1000.0,
        pose[1] * 1000.0,
        pose[2] * 1000.0,
        math.degrees(pose[3]),
        math.degrees(pose[4]),
        math.degrees(pose[5]),
    ]


def _pose_from_sdk_xyzabc(pose: list[float]) -> list[float]:
    return [
        pose[0] / 1000.0,
        pose[1] / 1000.0,
        pose[2] / 1000.0,
        math.radians(pose[3]),
        math.radians(pose[4]),
        math.radians(pose[5]),
    ]


def _flatten_matrix(matrix: list[list[float]]) -> list[float]:
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise RuntimeError("Tianji pose matrix must be 4x4")
    return [float(value) for row in matrix for value in row]
