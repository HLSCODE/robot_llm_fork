from __future__ import annotations

from contextlib import contextmanager
import unittest

from src.application.balance import (
    LLMBalanceDisplayRecognizer,
    ManagedBalanceCameraCapture,
    register_balance_reader,
)
from src.configuration.settings import CameraProfile, CameraRole, VisionSettings
from src.devices import BalanceReader, DeviceOperationError, DeviceRuntime
from src.devices.runtime.ids import BALANCE
from src.devices.sensors.balance import VisionBalanceReader
from src.llm import LLMChatResult


class _Camera:
    def __init__(self, frames: list[tuple[str, str, bytes]]) -> None:
        self.frames = frames

    def get_latest_jpegs(self) -> list[tuple[str, str, bytes]]:
        return self.frames


class _CameraAccess:
    def __init__(self, camera: _Camera) -> None:
        self.camera = camera
        self.released = False

    @contextmanager
    def open(self, purpose: str):
        self.purpose = purpose
        try:
            yield self.camera
        finally:
            self.released = True


class _VisionTask:
    def __init__(self) -> None:
        self.images = ()
        self.profile = None

    async def observe(self, *, images, profile, **_kwargs) -> LLMChatResult:
        self.images = images
        self.profile = profile
        return LLMChatResult(text="12.340", model="test", provider="test")


class _LLMRegistry:
    def __init__(self) -> None:
        self.vision_fusion = _VisionTask()


class BalanceReaderTests(unittest.TestCase):
    def test_vision_provider_returns_normalized_reading(self) -> None:
        reader = VisionBalanceReader(
            lambda: b"jpeg",
            lambda _jpeg: "重量：-1.250 g",
            clock=lambda: 42.0,
        )

        reading = reader.read_weight()

        self.assertEqual(-1.25, reading.weight_g)
        self.assertEqual(42.0, reading.captured_at)
        self.assertEqual("vision-llm", reading.provider)

    def test_vision_provider_rejects_unparseable_result(self) -> None:
        reader = VisionBalanceReader(lambda: b"jpeg", lambda _jpeg: "ERROR")

        with self.assertRaisesRegex(ValueError, "无法.*解析"):
            reader.read_weight()

    def test_managed_capture_selects_named_camera_and_releases_session(self) -> None:
        access = _CameraAccess(
            _Camera(
                [
                    ("one", "overview", b"overview"),
                    ("two", "balance", b"balance"),
                ]
            )
        )
        capture = ManagedBalanceCameraCapture(
            access,  # type: ignore[arg-type]
            camera_name="balance",
        )

        self.assertEqual(b"balance", capture())
        self.assertEqual("balance-reading", access.purpose)
        self.assertTrue(access.released)

    def test_managed_capture_requires_an_explicit_camera_identity(self) -> None:
        access = _CameraAccess(_Camera([]))

        with self.assertRaisesRegex(ValueError, "camera name must not be empty"):
            ManagedBalanceCameraCapture(
                access,  # type: ignore[arg-type]
                camera_name=" ",
            )

    def test_llm_recognizer_uses_balance_profile_and_jpeg_content(self) -> None:
        llm = _LLMRegistry()
        recognizer = LLMBalanceDisplayRecognizer(llm)  # type: ignore[arg-type]

        self.assertEqual("12.340", recognizer(b"jpeg"))
        self.assertEqual("balance_reading", llm.vision_fusion.profile.name)
        image = llm.vision_fusion.images[0]
        self.assertEqual("image", image.type)
        self.assertEqual("image/jpeg", image.mime_type)

    def test_simulation_registers_balance_capability(self) -> None:
        runtime = DeviceRuntime()
        register_balance_reader(
            runtime,
            camera_access=None,  # type: ignore[arg-type]
            llm=None,  # type: ignore[arg-type]
            settings=VisionSettings(),
            simulation=True,
        )

        reader = runtime.require(BALANCE, BalanceReader)

        self.assertEqual(0.0, reader.read_weight().weight_g)

    def test_hardware_balance_reader_requires_a_balance_camera_role(self) -> None:
        runtime = DeviceRuntime()
        settings = VisionSettings(
            cameras=(
                CameraProfile(
                    name="overview",
                    provider="realsense",
                    device_id="serial-overview",
                    roles=(CameraRole.VISION_CAPTURE.value,),
                ),
            )
        )
        register_balance_reader(
            runtime,
            camera_access=_CameraAccess(_Camera([])),  # type: ignore[arg-type]
            llm=_LLMRegistry(),  # type: ignore[arg-type]
            settings=settings,
            simulation=False,
        )

        with self.assertRaises(DeviceOperationError) as raised:
            runtime.require(BALANCE, BalanceReader)

        self.assertIn("role 'balance'", raised.exception.diagnostic_message)


if __name__ == "__main__":
    unittest.main()
