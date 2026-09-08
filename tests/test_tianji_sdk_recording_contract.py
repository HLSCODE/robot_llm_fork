"""Exercise the installed wheel's button callbacks without connecting hardware.

Private SDK callbacks are exercised only here, as regression stimuli; production
code must continue using the public RobotClient API.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from src.configuration.settings import TianjiRobotSettings
from src.devices.robots.tianji.driver import _OfficialTianjiSdkRuntime


class TianjiSdkRecordingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from tj_robot_proj import Arm
            from tj_robot_proj.robot_client import _ButtonStateModel
        except ImportError:
            self.skipTest("platform Tianji SDK wheel is not installed")
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.native = MagicMock()
        self.native.collect_data.return_value = True
        self.native.stop_collect_data.return_value = True
        self.native.save_collected_data.return_value = True
        patcher = patch("tj_robot_proj.robot_client.NativeSession", return_value=self.native)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings = TianjiRobotSettings()
        self.runtime = _OfficialTianjiSdkRuntime(
            controller_ip=settings.controller_ip,
            subscription_interval_seconds=settings.subscription_interval_seconds,
            left_base_transform=settings.left_base_transform,
            right_base_transform=settings.right_base_transform,
            left_tool_transform=settings.left_tool_transform,
            right_tool_transform=settings.right_tool_transform,
            joint_limits_rad=settings.joint_limits_rad,
            trajectory_directory=self.temporary.name,
        )
        self.addCleanup(self.runtime.close)
        self.button = _ButtonStateModel(self.runtime._client, Arm.LEFT)
        # No initialize(): neither the native driver nor button monitor is started.

    def test_button_release_does_not_stop_gui_collection(self) -> None:
        self.assertTrue(self.runtime.collect_data())
        for _ in range(2):
            self.button.on_button_pressed()
            self.button.on_button_released()
        self.native.collect_data.assert_called_once_with()
        self.native.stop_collect_data.assert_not_called()
        self.native.save_collected_data.assert_not_called()
        destination = str(Path(self.temporary.name) / "gui-recording")
        self.assertTrue(self.runtime.save_recording(destination))
        self.native.stop_collect_data.assert_called_once_with()
        self.native.save_collected_data.assert_called_once_with(str(Path(destination).resolve()))

    def test_button_recording_uses_configured_directory(self) -> None:
        self.button.on_button_pressed()
        self.assertFalse(self.runtime.collect_data())
        self.button.on_button_released()
        self.native.collect_data.assert_called_once_with()
        self.native.stop_collect_data.assert_called_once_with()
        self.native.save_collected_data.assert_called_once_with(
            str(Path(self.temporary.name).resolve()),
        )
