from __future__ import annotations

from typing import Protocol

from ...runtime.arm_models import (
    ArmId,
    ArmState,
    CartesianPose,
    MotionMode,
    MotionOptions,
    RobotOperationError,
)
from ...runtime.models import StopMode


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

    def stop_arm(self, arm: str, *, emergency: bool) -> bool: ...

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

    @property
    def supported_stop_modes(self) -> frozenset[StopMode]:
        return frozenset({StopMode.QUICK, StopMode.EMERGENCY})

    def stop(self, mode: StopMode) -> None:
        if mode not in self.supported_stop_modes:
            raise ValueError(f"unsupported robot stop mode: {mode}")
        failures: list[str] = []
        for arm in ArmId:
            try:
                succeeded = self._driver.stop_arm(
                    self._arm_key(arm),
                    emergency=mode is StopMode.EMERGENCY,
                )
            except Exception as exc:
                failures.append(f"{arm.value}: {exc}")
            else:
                if not succeeded:
                    failures.append(f"{arm.value}: SDK rejected stop")
        if failures:
            raise RuntimeError(f"{mode.value} stop failed for " + "; ".join(failures))

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
                detail=(
                    "Tianji provider does not support blend_radius or "
                    "connected motion"
                ),
            )
        succeeded = self._driver.move_to_pose(
            self._arm_key(arm),
            pose.to_list(),
            linear=mode is MotionMode.LINEAR,
            velocity_percent=selected.velocity_percent,
            blocking=selected.blocking,
        )
        if not succeeded:
            raise RobotOperationError("move_to_pose", arm, detail="SDK rejected motion")

    def read_arm_state(self, arm: ArmId) -> ArmState:
        payload = self._driver.read_state(self._arm_key(arm))
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
