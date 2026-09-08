"""Controller-wide Tianji collection with explicit per-arm drag selection."""

from pathlib import Path
from typing import Protocol

from ...runtime.arm_models import ArmId, TrajectoryArtifact, TrajectoryRecordingResult


class TianjiTeaching(Protocol):
    def start_recording(self) -> None: ...
    def save_recording(self, directory: str | Path) -> None: ...
    def start_drag_teaching(self, arm: ArmId) -> None: ...
    def stop_drag_teaching(self, arm: ArmId) -> None: ...


class TianjiTrajectoryRecorder:
    def __init__(self, robot: TianjiTeaching) -> None:
        self._robot = robot
        self._collecting = False

    @property
    def scope_description(self) -> str:
        return (
            "拖拽所选机械臂，同时采集左右双臂；保存原始数据、TXT 和 FMV。"
            "请勿同时使用末端按钮录制。取消时数据保留在 recovery 目录。"
        )

    def start(self, arm: ArmId) -> None:
        self._robot.start_drag_teaching(arm)
        try:
            self._robot.start_recording()
        except Exception:
            self._robot.stop_drag_teaching(arm)
            raise
        self._collecting = True

    def finish(self, arm: ArmId, target: Path) -> TrajectoryRecordingResult:
        self._robot.stop_drag_teaching(arm)
        directory = target.with_suffix("")
        directory.mkdir(parents=True, exist_ok=False)
        self._robot.save_recording(directory)
        self._collecting = False
        files = tuple(
            TrajectoryArtifact(path, _file_arm(path))
            for path in sorted(directory.rglob("*")) if path.is_file()
        )
        matching = [item.path for item in files
                    if item.arm is arm and item.path.suffix.lower() == ".fmv"]
        if len(matching) != 1:
            raise RuntimeError(f"录制已保存至 {directory}，但所选臂没有唯一的 FMV 文件")
        with matching[0].open(encoding="utf-8-sig") as stream:
            header = stream.readline().strip()
        if not header.startswith("PoinType=9@"):
            raise ValueError(f"无效 FMV 文件头: {matching[0]}")
        return TrajectoryRecordingResult(matching[0], int(header.split("@", 1)[1]), files)

    def cancel(self, arm: ArmId, recovery_directory: Path, *, restore_mode: bool = True) -> None:
        if restore_mode:
            self._robot.stop_drag_teaching(arm)
        if self._collecting:
            # SDK has no stop-without-save API. Preserve data instead of discarding it.
            self._robot.save_recording(recovery_directory)
            self._collecting = False


def _file_arm(path: Path) -> ArmId | None:
    stem = path.stem.upper()
    if stem.endswith(("_L", "_LEFT_ARM")):
        return ArmId.LEFT
    if stem.endswith(("_R", "_RIGHT_ARM")):
        return ArmId.RIGHT
    return None
