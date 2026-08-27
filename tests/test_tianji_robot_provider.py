from __future__ import annotations

import unittest
from collections.abc import Sequence

from src.configuration.settings import (
    RealManRobotSettings,
    RobotConfiguration,
    RobotSettings,
    TianjiRobotSettings,
)
from src.devices.robots.registry import resolve_robot_provider
from src.devices.robots.tianji.adapter import TianjiRobotAdapter
from src.devices.robots.tianji.driver import TianjiRobotDriver
from src.devices.robots.tianji.provider import TianjiProviderSettings
from src.devices.runtime.arm_models import (
    ArmId,
    CartesianPose,
    MotionMode,
    MotionOptions,
    RobotOperationError,
)
from src.devices.runtime.models import DeviceCapability, StopMode


class _FakeSdkRuntime:
    def __init__(self) -> None:
        self.initialize_count = 0
        self.close_count = 0
        self.moves: list[tuple[str, tuple[float, ...], int, bool]] = []
        self.states = {
            "A": {
                "pose": [0.1, 0.2, 0.3, 0.01, 0.02, 0.03],
                "joints": [1.0] * 7,
                "error_code": 0,
            },
            "B": {
                "pose": [0.4, 0.5, 0.6, 0.04, 0.05, 0.06],
                "joints": [2.0] * 7,
                "error_code": 0,
            },
        }

    def initialize(self) -> None:
        self.initialize_count += 1

    def move_linear(
        self,
        arm: str,
        pose: Sequence[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None:
        self.moves.append((arm, tuple(pose), velocity_percent, blocking))

    def read_state(self, arm: str) -> dict[str, object]:
        return self.states[arm]

    def close(self) -> None:
        self.close_count += 1


class _NativeSdkFailure(RuntimeError):
    native_code = 37
    detail = "controller rejected target"


class _FailingMoveRuntime(_FakeSdkRuntime):
    def move_linear(
        self,
        arm: str,
        pose: Sequence[float],
        *,
        velocity_percent: int,
        blocking: bool,
    ) -> None:
        del arm, pose, velocity_percent, blocking
        raise _NativeSdkFailure


def _driver(
    runtime: _FakeSdkRuntime | None = None,
) -> tuple[TianjiRobotDriver, _FakeSdkRuntime]:
    selected = runtime or _FakeSdkRuntime()
    identity = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    driver = TianjiRobotDriver(
        "192.168.1.190",
        subscription_interval_seconds=0.01,
        left_base_transform=identity,
        right_base_transform=identity,
        left_tool_transform=identity,
        right_tool_transform=identity,
        joint_limits_rad=((-3.14, 3.14),) * 7,
        sdk_runtime=selected,
    )
    return driver, selected


class TianjiProviderTests(unittest.TestCase):
    def test_registry_exposes_only_public_sdk_capabilities(self) -> None:
        provider = resolve_robot_provider(RobotSettings(provider="tianji"))

        self.assertEqual("tianji", provider.name)
        self.assertIn(DeviceCapability.ARM_MOTION, provider.capabilities)
        self.assertIn(DeviceCapability.ARM_STATE, provider.capabilities)
        self.assertNotIn(DeviceCapability.QUICK_STOP, provider.capabilities)
        self.assertNotIn(DeviceCapability.EMERGENCY_STOP, provider.capabilities)
        self.assertNotIn(DeviceCapability.GRIPPER, provider.capabilities)
        self.assertNotIn(DeviceCapability.TRAJECTORY, provider.capabilities)

    def test_provider_settings_include_sdk_session_configuration(self) -> None:
        settings = TianjiProviderSettings.from_settings(
            RobotConfiguration(
                common=RobotSettings(provider="tianji"),
                realman=RealManRobotSettings(),
                tianji=TianjiRobotSettings(
                    model="tj-dual-7",
                    controller_ip="10.0.0.8",
                    subscription_interval_seconds=0.02,
                ),
            )
        )

        self.assertEqual("10.0.0.8", settings.controller_ip)
        self.assertEqual("tj-dual-7", settings.model)
        self.assertEqual(0.02, settings.subscription_interval_seconds)
        self.assertEqual(7, len(settings.joint_limits_rad))


class TianjiDriverTests(unittest.TestCase):
    def test_driver_initializes_and_preserves_public_sdk_units(self) -> None:
        driver, runtime = _driver()
        self.addCleanup(driver.close)

        state = driver.read_state("A")
        succeeded = driver.move_to_pose(
            "B",
            [0.4, 0.5, 0.6, 0.1, 0.2, 0.3],
            linear=True,
            velocity_percent=20,
            blocking=False,
        )

        self.assertTrue(succeeded)
        self.assertEqual(1, runtime.initialize_count)
        self.assertEqual([0.1, 0.2, 0.3, 0.01, 0.02, 0.03], state["pose"])
        self.assertEqual([1.0] * 7, state["joints"])
        self.assertEqual(
            ("B", (0.4, 0.5, 0.6, 0.1, 0.2, 0.3), 20, False),
            runtime.moves[-1],
        )

    def test_close_is_idempotent(self) -> None:
        driver, runtime = _driver()

        driver.close()
        driver.close()

        self.assertEqual(1, runtime.close_count)

    def test_adapter_maps_arm_ids_and_state(self) -> None:
        driver, _ = _driver()
        self.addCleanup(driver.close)
        adapter = TianjiRobotAdapter(driver, default_motion=MotionOptions())

        state = adapter.read_arm_state(ArmId.RIGHT)
        adapter.move_to_pose(
            ArmId.LEFT,
            CartesianPose(0.1, 0.2, 0.3, 0.0, 0.0, 0.0),
            MotionMode.LINEAR,
            MotionOptions(blocking=False),
        )

        self.assertEqual(ArmId.RIGHT, state.arm)
        assert state.joints is not None
        self.assertEqual((2.0,) * 7, state.joints.positions_deg)

    def test_adapter_rejects_capabilities_missing_from_public_sdk(self) -> None:
        driver, _ = _driver()
        self.addCleanup(driver.close)
        adapter = TianjiRobotAdapter(driver, default_motion=MotionOptions())

        with self.assertRaisesRegex(RobotOperationError, "only exposes linear"):
            adapter.move_to_pose(
                ArmId.LEFT,
                CartesianPose(0.1, 0.2, 0.3, 0.0, 0.0, 0.0),
                MotionMode.JOINT,
            )
        with self.assertRaisesRegex(ValueError, "does not expose a public stop"):
            adapter.stop(StopMode.QUICK)

    def test_adapter_preserves_sdk_error_code_and_detail(self) -> None:
        driver, _ = _driver(_FailingMoveRuntime())
        self.addCleanup(driver.close)
        adapter = TianjiRobotAdapter(driver, default_motion=MotionOptions())

        with self.assertRaises(RobotOperationError) as raised:
            adapter.move_to_pose(
                ArmId.LEFT,
                CartesianPose(0.1, 0.2, 0.3, 0.0, 0.0, 0.0),
                MotionMode.LINEAR,
            )

        self.assertEqual(37, raised.exception.code)
        self.assertEqual("controller rejected target", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
