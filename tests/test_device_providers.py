from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.configuration.settings import CameraProfile, VisionSettings
from src.devices.cameras.registry import (
    CAMERA_PROVIDERS,
    resolve_camera_provider,
)
from src.devices.cameras.providers.opencv import create_opencv_camera
from src.devices.cameras.providers.realsense import create_realsense_camera
from src.devices.displays.registry import (
    EXPRESSION_DISPLAY_PROVIDERS,
    resolve_expression_display_provider,
)
from src.devices.motion.mobile_base.tcp.adapter import TcpMobileBaseAdapter
from src.devices.runtime.models import DeviceCapability, DeviceInitializationError


class CameraProviderTests(unittest.TestCase):
    def test_built_in_providers_share_camera_capability(self) -> None:
        self.assertEqual({"opencv", "realsense"}, set(CAMERA_PROVIDERS))
        for provider in CAMERA_PROVIDERS.values():
            self.assertIn(DeviceCapability.CAMERA, provider.capabilities)

    def test_unknown_provider_fails_instead_of_falling_back(self) -> None:
        with self.assertRaisesRegex(
            DeviceInitializationError,
            "unsupported camera provider: unknown",
        ):
            resolve_camera_provider(
                VisionSettings(
                    cameras=(
                        CameraProfile(
                            name="fixture",
                            provider="unknown",
                            device_id="fixture-device",
                        ),
                    )
                )
            )

    def test_empty_camera_catalog_does_not_imply_realsense(self) -> None:
        with self.assertRaisesRegex(
            DeviceInitializationError,
            "camera catalog must contain at least one profile",
        ):
            resolve_camera_provider(VisionSettings())

    def test_opencv_provider_is_created_idle(self) -> None:
        manager = MagicMock()
        settings = VisionSettings(cameras=(CameraProfile("fixture", "opencv", "0"),))

        with patch(
            "src.devices.cameras.providers.opencv.OpenCVCameraManager",
            return_value=manager,
        ):
            created = create_opencv_camera(settings)

        self.assertIs(manager, created)
        manager.start.assert_not_called()

    def test_realsense_provider_is_created_idle(self) -> None:
        manager = MagicMock()
        settings = VisionSettings(cameras=(CameraProfile("fixture", "realsense", "serial"),))

        with patch(
            "src.devices.cameras.providers.realsense.RealSenseManager",
            return_value=manager,
        ):
            created = create_realsense_camera(settings)

        self.assertIs(manager, created)
        manager.start.assert_not_called()


class DisplayProviderTests(unittest.TestCase):
    def test_t5l_provider_is_registered_explicitly(self) -> None:
        provider = resolve_expression_display_provider("t5l_dgusii")
        self.assertIs(provider, EXPRESSION_DISPLAY_PROVIDERS["t5l_dgusii"])

    def test_unknown_provider_fails_without_dynamic_import(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported expression display"):
            resolve_expression_display_provider("missing")


class _FakeMobileBaseClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.connected = False
        self.responses = list(responses)
        self.commands: list[dict[str, object]] = []
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def send_command(self, command) -> None:
        self.commands.append(dict(command))

    def receive_response(self) -> dict[str, object]:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class TcpMobileBaseAdapterTests(unittest.TestCase):
    def test_adapter_translates_move_and_ignores_status_updates(self) -> None:
        client = _FakeMobileBaseClient(
            [
                {"cmd": 1, "execute": 0.3},
                {"cmd": 1, "result": True},
            ]
        )
        adapter = TcpMobileBaseAdapter(client)

        self.assertTrue(adapter.move_to_position(4, 2))
        self.assertEqual([{"cmd": 1, "id": 4, "cid": 2}], client.commands)
        self.assertTrue(adapter.get_last_result())

    def test_adapter_rejects_a_mismatched_response(self) -> None:
        client = _FakeMobileBaseClient([{"cmd": 1, "result": True}])
        adapter = TcpMobileBaseAdapter(client)

        with self.assertRaisesRegex(ValueError, "does not match"):
            adapter.move_slowly(1.0, 2.0, 3.0)


if __name__ == "__main__":
    unittest.main()
