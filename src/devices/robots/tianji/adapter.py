from __future__ import annotations

from typing import Protocol
from pathlib import Path

from ...runtime.arm_models import (
    ArmId,
    ArmState,
    CartesianPose,
    JointVector,
    MotionMode,
    MotionOptions,
    RobotOperationError,
)
from ...runtime.models import StopMode
from .recording import TianjiTrajectoryRecorder


class TianjiDriver(Protocol):
    def move_to_pose(
        self,
        arm: str,
        pose: list[float],
        *,
        linear: bool,
        velocity_percent: int,
        blocking: bool,
    ) -> bool: ...

    def read_state(self, arm: str) -> dict[str, object]: ...

    def move_to_joints(
        self, arm: str, joints: list[float], *, velocity_percent: int, blocking: bool
    ) -> None: ...

    def move_joint_increment(
        self, arm: str, delta_deg: list[float], *, velocity_percent: int, blocking: bool
    ) -> None: ...

    def move_pose_increment(
        self, arm: str, delta_pose: list[float], *, velocity_percent: int, blocking: bool
    ) -> None: ...

    def quick_stop(self, arm: str) -> None: ...

    def emergency_stop(self) -> None: ...

    def set_drag_mode(self, arm: str, *, enabled: bool) -> None: ...

    def start_recording(self) -> None: ...

    def save_recording(self, directory: str | Path) -> None: ...

    def run_trajectory(self, arm: str, path: str | Path, *, blocking: bool = True) -> None: ...

    def close(self) -> None: ...


class TianjiRobotAdapter:
    """Translate the Tianji A/B arm SDK into project-wide robot contracts."""

    def __init__(
        self,
        driver: TianjiDriver,
        *,
        default_motion: MotionOptions,
    ) -> None:
        self._driver = driver
        self._default_motion = default_motion
        self._trajectory_recorder = TianjiTrajectoryRecorder(self)

    @property
    def trajectory_recorder(self) -> TianjiTrajectoryRecorder:
        return self._trajectory_recorder

    @property
    def supported_stop_modes(self) -> frozenset[StopMode]:
        return frozenset({StopMode.QUICK, StopMode.EMERGENCY})

    def stop(self, mode: StopMode) -> None:
        if mode not in self.supported_stop_modes:
            raise ValueError(f"unsupported robot stop mode: {mode}")
        if mode is StopMode.EMERGENCY:
            self._driver.emergency_stop()
            return
        failures: list[Exception] = []
        for arm in ArmId:
            try:
                self._driver.quick_stop(self._arm_key(arm))
            except Exception as exc:
                failures.append(_operation_error("quick_stop", arm, exc))
        if failures:
            raise ExceptionGroup("Tianji quick stop failed", failures)

    def move_to_joints(
        self,
        arm: ArmId,
        joints: JointVector,
        options: MotionOptions | None = None,
    ) -> None:
        selected = options or self._default_motion
        if selected.blend_radius or selected.connected:
            raise RobotOperationError(
                "move_to_joints",
                arm,
                detail="Tianji does not support blended motion",
            )
        try:
            self._driver.move_to_joints(
                self._arm_key(arm),
                joints.to_list(),
                velocity_percent=selected.velocity_percent,
                blocking=selected.blocking,
            )
        except Exception as exc:
            raise _operation_error("move_to_joints", arm, exc) from exc

    def start_drag_teaching(self, arm: ArmId) -> None:
        try:
            self._driver.set_drag_mode(self._arm_key(arm), enabled=True)
        except Exception as exc:
            raise _operation_error("start_drag_teaching", arm, exc) from exc

    def move_joint_increment(
        self,
        arm: ArmId,
        delta: JointVector,
        options: MotionOptions | None = None,
    ) -> None:
        selected = options or self._default_motion
        if selected.blend_radius or selected.connected:
            raise RobotOperationError(
                "move_joint_increment",
                arm,
                detail="Tianji does not support blended motion",
            )
        try:
            self._driver.move_joint_increment(
                self._arm_key(arm),
                delta.to_list(),
                velocity_percent=selected.velocity_percent,
                blocking=selected.blocking,
            )
        except Exception as exc:
            raise _operation_error("move_joint_increment", arm, exc) from exc

    def move_pose_increment(
        self,
        arm: ArmId,
        delta: CartesianPose,
        options: MotionOptions | None = None,
    ) -> None:
        """Apply an increment expressed in the current end-effector frame."""
        selected = options or self._default_motion
        if selected.blend_radius or selected.connected:
            raise RobotOperationError(
                "move_pose_increment",
                arm,
                detail="Tianji does not support blended motion",
            )
        try:
            self._driver.move_pose_increment(
                self._arm_key(arm),
                delta.to_list(),
                velocity_percent=selected.velocity_percent,
                blocking=selected.blocking,
            )
        except Exception as exc:
            raise _operation_error("move_pose_increment", arm, exc) from exc

    def stop_drag_teaching(self, arm: ArmId) -> None:
        try:
            self._driver.set_drag_mode(self._arm_key(arm), enabled=False)
        except Exception as exc:
            raise _operation_error("stop_drag_teaching", arm, exc) from exc

    def start_recording(self) -> None:
        """Start SDK controller-wide collection for both arms."""
        self._driver.start_recording()

    def save_recording(self, directory: str | Path) -> None:
        """Stop collection and save raw, split TXT and FMV files to a directory."""
        self._driver.save_recording(directory)

    def run_trajectory(self, arm: ArmId, path: str | Path, *, blocking: bool = True) -> None:
        try:
            self._driver.run_trajectory(self._arm_key(arm), path, blocking=blocking)
        except Exception as exc:
            raise _operation_error("run_trajectory", arm, exc) from exc

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
        if selected.blend_radius != 0 or selected.connected:
            raise RobotOperationError(
                "move_to_pose",
                arm,
                detail=("Tianji provider does not support blend_radius or connected motion"),
            )
        try:
            succeeded = self._driver.move_to_pose(
                self._arm_key(arm),
                pose.to_list(),
                linear=mode is MotionMode.LINEAR,
                velocity_percent=selected.velocity_percent,
                blocking=selected.blocking,
            )
        except RobotOperationError:
            raise
        except Exception as exc:
            raise _operation_error("move_to_pose", arm, exc) from exc
        if not succeeded:
            raise RobotOperationError("move_to_pose", arm, detail="SDK rejected motion")

    def read_arm_state(self, arm: ArmId) -> ArmState:
        try:
            payload = self._driver.read_state(self._arm_key(arm))
        except RobotOperationError:
            raise
        except Exception as exc:
            raise _operation_error("read_arm_state", arm, exc) from exc
        try:
            raw_error_code = payload.get("error_code", 0)
            if not isinstance(raw_error_code, (int, float, str)):
                raise TypeError("error_code must be numeric")
            error_code = int(raw_error_code)
            raw_pose = payload["pose"]
            if not isinstance(raw_pose, (list, tuple)):
                raise TypeError("pose must be a sequence")
            pose = CartesianPose.from_iterable(raw_pose)
            raw_joints = payload["joints"]
            if not isinstance(raw_joints, (list, tuple)):
                raise TypeError("joints must be a sequence")
        except (KeyError, TypeError, ValueError) as exc:
            raise RobotOperationError(
                "read_arm_state",
                arm,
                detail=f"invalid Tianji state payload: {exc}",
            ) from exc
        if error_code:
            raise RobotOperationError(
                "read_arm_state",
                arm,
                code=error_code,
                detail="device reported an error",
            )
        from ...runtime.arm_models import JointVector

        return ArmState(
            arm=arm,
            pose=pose,
            joints=JointVector.from_iterable(raw_joints),
            device_error_code=error_code,
        )

    def try_read_arm_state(self, arm: ArmId) -> ArmState | None:
        try:
            return self.read_arm_state(arm)
        except RobotOperationError:
            return None

    def close(self) -> None:
        self._driver.close()

    @staticmethod
    def _arm_key(arm: ArmId) -> str:
        if not isinstance(arm, ArmId):
            raise TypeError("arm must be an ArmId")
        return "A" if arm is ArmId.LEFT else "B"


def _operation_error(
    operation: str,
    arm: ArmId,
    error: Exception,
) -> RobotOperationError:
    native_code = getattr(error, "native_code", None)
    code = native_code if isinstance(native_code, int) else None
    sdk_detail = str(getattr(error, "detail", "")).strip()
    detail = sdk_detail or str(error).strip() or type(error).__name__
    return RobotOperationError(operation, arm, code=code, detail=detail)
