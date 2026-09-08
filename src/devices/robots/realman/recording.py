"""RealMan single-arm recording adapted independently from playback."""

from pathlib import Path
from typing import Protocol

from ...runtime.arm_models import (
    ArmId, TrajectoryArtifact, TrajectoryRecordingResult, TrajectorySaveResult,
)


class RealManTeaching(Protocol):
    def start_drag_teaching(self, arm: ArmId) -> None: ...
    def stop_drag_teaching(self, arm: ArmId) -> None: ...
    def save_trajectory(self, arm: ArmId, path: str | Path) -> TrajectorySaveResult: ...


class RealManTrajectoryRecorder:
    def __init__(self, robot: RealManTeaching) -> None:
        self._robot = robot

    @property
    def scope_description(self) -> str:
        return "仅录制所选机械臂，保存为单个轨迹文件。"

    def start(self, arm: ArmId) -> None:
        self._robot.start_drag_teaching(arm)

    def finish(self, arm: ArmId, target: Path) -> TrajectoryRecordingResult:
        self._robot.stop_drag_teaching(arm)
        result = self._robot.save_trajectory(arm, target)
        return TrajectoryRecordingResult(
            result.path, result.point_count, (TrajectoryArtifact(result.path, arm),),
        )

    def cancel(self, arm: ArmId, recovery_directory: Path, *, restore_mode: bool = True) -> None:
        if restore_mode:
            self._robot.stop_drag_teaching(arm)
