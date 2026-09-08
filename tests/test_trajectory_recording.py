from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from src.application.services import TrajectoryTeachingService
from src.devices.robots.tianji.recording import TianjiTrajectoryRecorder
from src.devices.runtime.arm_models import ArmId
from src.devices.runtime.models import DeviceCapability
from src.devices.runtime.resources import ResourceArbiter
from src.devices.runtime.runtime import DeviceRegistration, DeviceRuntime
from src.persistence.trajectory_storage import TrajectoryStorage


class FakeTianjiTeaching:
    def __init__(self) -> None:
        self.collecting = False
        self.fail_save = False
        self.calls: list[str] = []

    def start_drag_teaching(self, arm: ArmId) -> None:
        self.calls.append(f"drag:{arm.value}")

    def stop_drag_teaching(self, arm: ArmId) -> None:
        self.calls.append(f"normal:{arm.value}")

    def start_recording(self) -> None:
        self.collecting = True
        self.calls.append("collect")

    def save_recording(self, directory: str | Path) -> None:
        if self.fail_save:
            raise RuntimeError("save failed")
        assert self.collecting
        directory = Path(directory) / "timestamp"
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("sample_L.fmv", "sample_R.fmv"):
            (directory / name).write_text("PoinType=9@12\n", encoding="utf-8")
        for name in ("sample_left_arm.txt", "sample_right_arm.txt", "sample_raw.txt"):
            (directory / name).write_text("recorded", encoding="utf-8")
        self.collecting = False
        self.calls.append("save")


class TrajectoryRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.robot = FakeTianjiTeaching()
        self.recorder = TianjiTrajectoryRecorder(self.robot)
        runtime = DeviceRuntime()
        runtime.register(DeviceRegistration(
            device_id="robot-system",
            capabilities=frozenset({DeviceCapability.TRAJECTORY_RECORDING}),
            factory=lambda: SimpleNamespace(trajectory_recorder=self.recorder),
            close=lambda _device: None,
        ))
        self.resources = ResourceArbiter()
        self.service = TrajectoryTeachingService(
            runtime, self.resources, TrajectoryStorage(self.root),
        )

    def test_recording_only_device_saves_both_arms_and_can_record_again(self) -> None:
        self.service.start("left")
        self.assertFalse(self.service.supports_playback)
        self.assertTrue(self.service.active)
        self.assertIsNotNone(self.resources.owner_of("robot-system"))
        result = self.service.stop_and_save()
        self.assertEqual(5, len(result.files))
        self.assertEqual(12, result.point_count)
        self.assertEqual("sample_L.fmv", result.path.name)
        self.assertEqual({ArmId.LEFT, ArmId.RIGHT, None}, {item.arm for item in result.files})
        self.assertFalse(self.service.active)
        self.assertIsNone(self.resources.owner_of("robot-system"))
        self.service.start("left")
        second = self.service.stop_and_save()
        self.assertNotEqual(result.path, second.path)
        self.assertTrue(result.path.is_file())

    def test_cancel_stops_collection_and_preserves_recovery_files(self) -> None:
        self.service.start("right")
        self.service.cancel()
        self.service.cancel()
        self.assertFalse(self.robot.collecting)
        self.assertFalse(self.service.active)
        self.assertEqual(2, len(list((self.root / "recovery").rglob("*.fmv"))))

    def test_failed_save_keeps_lease_until_successful_cancel(self) -> None:
        self.service.start("left")
        self.robot.fail_save = True
        with self.assertRaises(Exception):
            self.service.stop_and_save()
        self.assertTrue(self.service.active)
        self.assertIsNotNone(self.resources.owner_of("robot-system"))
        self.robot.fail_save = False
        self.service.cancel()
        self.assertIsNone(self.resources.owner_of("robot-system"))

    def test_safety_stop_does_not_reenable_position_mode(self) -> None:
        self.service.start("left")
        self.service.release_after_safety_stop()
        self.assertNotIn("normal:left", self.robot.calls)
        self.assertFalse(self.robot.collecting)
        self.assertFalse(self.service.active)
