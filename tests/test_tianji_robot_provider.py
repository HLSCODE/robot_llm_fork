from __future__ import annotations

import math
import unittest

from src.configuration.settings import RobotSettings
from src.devices.robots.registry import resolve_robot_provider
from src.devices.robots.tianji.adapter import TianjiRobotAdapter
from src.devices.robots.tianji.driver import TianjiRobotDriver
from src.devices.robots.tianji.provider import TianjiProviderSettings
from src.devices.runtime.arm_models import (
    ArmId,
    CartesianPose,
    MotionMode,
    MotionOptions,
)
from src.devices.runtime.models import DeviceCapability, StopMode


class _FakeIkParameters:
    def __init__(self) -> None:
        self.target: list[float] = []
        self.reference: list[float] = []
        self.zsp_type = -1

    def set_input_ik_target_tcp(self, matrix: list[float]) -> None:
        self.target = matrix

    def set_input_ik_ref_joint(self, values: list[float]) -> None:
        self.reference = values

    def set_input_ik_zsp_type(self, value: int) -> None:
        self.zsp_type = value

    def get_output_ret_joint(self) -> list[float]:
        return [11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]


class _FakeKinematics:
    def __init__(self) -> None:
        self.linear_calls: list[tuple[list[float], list[float], float, float]] = []
        self.destroyed: list[object] = []

    def load_config(self, arm_type: int, config_path: str) -> object:
        return {"arm_type": arm_type, "config_path": config_path}

    def initial_kine(
        self,
        robot_type: int,
        dh: list[object],
        pnva: list[object],
        j67: list[object],
    ) -> object:
        del robot_type, dh, pnva, j67
        return True

    def fk(self, joints: list[float]) -> object:
        del joints
        return [
            [1.0, 0.0, 0.0, 100.0],
            [0.0, 1.0, 0.0, 200.0],
            [0.0, 0.0, 1.0, 300.0],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def mat4x4_to_xyzabc(self, pose_mat: list[list[float]]) -> object:
        del pose_mat
        return [100.0, 200.0, 300.0, 10.0, 20.0, 30.0]

    def xyzabc_to_mat4x4(self, xyzabc: list[float]) -> object:
        return [
            [1.0, 0.0, 0.0, xyzabc[0]],
            [0.0, 1.0, 0.0, xyzabc[1]],
            [0.0, 0.0, 1.0, xyzabc[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def ik(self, structure_data: _FakeIkParameters) -> _FakeIkParameters:
        return structure_data

    def movLA(
        self,
        start_xyzabc: list[float],
        end_xyzabc: list[float],
        ref_joints: list[float],
        vel: float,
        acc: float,
        freq_hz: int,
    ) -> tuple[list[list[float]], object | None]:
        del ref_joints, freq_hz
        self.linear_calls.append((start_xyzabc, end_xyzabc, vel, acc))
        return [[0.0] * 7], object()

    def destroy_point_set(self, pset: object) -> None:
        self.destroyed.append(pset)


class _FakeRobotSdk:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.released = 0
        self.outputs = [
            {"fb_joint_pos": [1.0] * 7, "fb_joint_vel": [0.0] * 7},
            {"fb_joint_pos": [2.0] * 7, "fb_joint_vel": [0.0] * 7},
        ]

    def connect(self, robot_ip: str) -> object:
        self.calls.append(("connect", robot_ip))
        return True

    def subscribe(self, state_buffer: object) -> dict[str, object]:
        del state_buffer
        return {
            "outputs": self.outputs,
            "states": [{"err_code": 0}, {"err_code": 0}],
        }

    def release_robot(self) -> object:
        self.released += 1
        return True

    def clear_set(self) -> object:
        self.calls.append(("clear_set",))
        return True

    def clear_error(self, arm: str) -> object:
        self.calls.append(("clear_error", arm))
        return True

    def set_state(self, arm: str, state: int) -> object:
        self.calls.append(("set_state", arm, state))
        return True

    def set_vel_acc(self, arm: str, velRatio: int, AccRatio: int) -> object:
        self.calls.append(("set_vel_acc", arm, velRatio, AccRatio))
        return True

    def send_cmd(self) -> object:
        self.calls.append(("send_cmd",))
        return True

    def set_joint_cmd_pose(self, arm: str, joints: list[float]) -> object:
        self.calls.append(("joint", arm, tuple(joints)))
        return True

    def setPln_Cart(self, arm: str, pset: object) -> object:
        self.calls.append(("linear", arm, pset))
        return True

    def soft_stop(self, arm: str) -> object:
        self.calls.append(("stop", arm))
        return True


def _driver() -> tuple[TianjiRobotDriver, _FakeRobotSdk, _FakeKinematics]:
    robot = _FakeRobotSdk()
    left = _FakeKinematics()
    driver = TianjiRobotDriver(
        "192.168.1.190",
        kinematics_config="test.MvKDCfg",
        acceleration_percent=40,
        linear_acceleration_m_s2=0.25,
        robot=robot,
        state_buffer=object(),
        kinematics={"A": left, "B": _FakeKinematics()},
        ik_parameter_factory=_FakeIkParameters,
    )
    return driver, robot, left


class TianjiProviderTests(unittest.TestCase):
    def test_registry_exposes_truthful_optional_capabilities(self) -> None:
        provider = resolve_robot_provider(RobotSettings(robot_provider="tianji"))

        self.assertEqual("tianji", provider.name)
        self.assertIn(DeviceCapability.ARM_MOTION, provider.capabilities)
        self.assertIn(DeviceCapability.ARM_STATE, provider.capabilities)
        self.assertNotIn(DeviceCapability.GRIPPER, provider.capabilities)
        self.assertNotIn(DeviceCapability.TRAJECTORY, provider.capabilities)

    def test_provider_settings_validate_tianji_specific_values(self) -> None:
        settings = TianjiProviderSettings.from_settings(
            RobotSettings(
                robot_provider="tianji",
                robot_model="tj-dual-7",
                tianji_controller_ip="10.0.0.8",
            )
        )

        self.assertEqual("10.0.0.8", settings.controller_ip)
        self.assertEqual("tj-dual-7", settings.model)


class TianjiDriverTests(unittest.TestCase):
    def test_state_converts_sdk_mm_degrees_to_metres_radians(self) -> None:
        driver, _, _ = _driver()
        self.addCleanup(driver.close)

        state = driver.read_state("A")

        self.assertEqual([1.0] * 7, state["joints"])
        pose = state["pose"]
        assert isinstance(pose, list)
        self.assertAlmostEqual(0.1, pose[0])
        self.assertAlmostEqual(math.radians(30.0), pose[5])

    def test_linear_motion_converts_units_and_owns_point_set_until_close(self) -> None:
        driver, robot, kinematics = _driver()
        succeeded = driver.move_to_pose(
            "A",
            [0.4, 0.5, 0.6, 0.1, 0.2, 0.3],
            linear=True,
            velocity_percent=20,
            blocking=False,
        )

        self.assertTrue(succeeded)
        _, target, velocity, acceleration = kinematics.linear_calls[-1]
        self.assertEqual([400.0, 500.0, 600.0], target[:3])
        self.assertAlmostEqual(math.degrees(0.3), target[5])
        self.assertEqual(100.0, velocity)
        self.assertEqual(250.0, acceleration)
        self.assertEqual(0, len(kinematics.destroyed))
        self.assertEqual("linear", robot.calls[-1][0])
        driver.close()
        self.assertEqual(1, len(kinematics.destroyed))

    def test_joint_path_uses_inverse_kinematics_result(self) -> None:
        driver, robot, _ = _driver()
        self.addCleanup(driver.close)

        succeeded = driver.move_to_pose(
            "A",
            [0.4, 0.5, 0.6, 0.1, 0.2, 0.3],
            linear=False,
            velocity_percent=30,
            blocking=False,
        )

        self.assertTrue(succeeded)
        joint_call = next(call for call in robot.calls if call[0] == "joint")
        self.assertEqual(tuple(range(11, 18)), joint_call[2])

    def test_close_is_idempotent_and_stop_maps_both_arms(self) -> None:
        driver, robot, _ = _driver()
        adapter = TianjiRobotAdapter(driver, default_motion=MotionOptions())

        adapter.stop(StopMode.QUICK)
        adapter.close()
        adapter.close()

        self.assertIn(("stop", "A"), robot.calls)
        self.assertIn(("stop", "B"), robot.calls)
        self.assertEqual(1, robot.released)

    def test_adapter_maps_project_arm_ids(self) -> None:
        driver, _, _ = _driver()
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
        self.assertEqual((2.0,) * 7, state.joints.positions_deg)


if __name__ == "__main__":
    unittest.main()
