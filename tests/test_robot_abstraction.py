from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.core.settings import ApplicationSettings, RobotSettings
from src.device_runtime import (
    ArmId,
    ArmTelemetryReader,
    CartesianPose,
    DeviceCapability,
    DeviceInitializationError,
    JointVector,
    MotionMode,
    MotionOptions,
    ResourceBusyError,
    RobotOperationError,
    RobotSystem,
    StopMode,
)
from src.device_runtime.adapters import (
    RealManRobotAdapter,
    RealManToolRackOptions,
    RealManToolRackSlot,
)
from src.device_runtime.factory import create_device_runtime
from src.device_runtime.fakes import SimulatedRobotSystem
from src.device_runtime.ids import ROBOT_SYSTEM
from src.device_runtime.robot_providers import (
    RealManProviderSettings,
    resolve_robot_provider,
)
from src.execution import ExecutionState


class _FakeSdkRobot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, object]]] = []
        self.state_code = 0
        self.quick_stop_code = 0
        self.emergency_stop_code = 0
        self.state = {
            "pose": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "joint": [1, 2, 3, 4, 5, 6],
            "error_code": 0,
        }

    def rm_movej_p(self, pose, **kwargs):
        self.calls.append(("movej", pose, kwargs))
        return 0

    def rm_movel(self, pose, **kwargs):
        self.calls.append(("movel", pose, kwargs))
        return 0

    def rm_get_current_arm_state(self):
        return self.state_code, self.state

    def rm_get_gripper_state(self):
        return 0, {
            "status": 1,
            "error": 0,
            "current_force": 500,
            "actpos": 250,
        }

    def rm_get_current_joint_current(self):
        return 0, [100 * (index + 1) for index in range(len(self.state["joint"]))]

    def rm_get_force_data(self):
        return 0, {"force_data": [1, 2, 3, 4, 5, 6]}

    def rm_set_gripper_release(self, **kwargs):
        self.calls.append(("gripper_release", None, kwargs))
        return 0

    def rm_set_gripper_pick_on(self, **kwargs):
        self.calls.append(("gripper_pick", None, kwargs))
        return 0

    def rm_set_gripper_position(self, position, **kwargs):
        self.calls.append(("gripper_position", position, kwargs))
        return 0

    def rm_movej_canfd(self, joints, follow, **kwargs):
        self.calls.append(
            ("movej_canfd", joints, {"follow": follow, **kwargs})
        )
        return 0

    def rm_movej(self, joints, velocity, radius, connect, block):
        self.calls.append(
            (
                "movej",
                joints,
                {
                    "velocity": velocity,
                    "radius": radius,
                    "connect": connect,
                    "block": block,
                },
            )
        )
        return 0

    def rm_start_drag_teach(self, mode):
        self.calls.append(("start_drag", mode, {}))
        return 0

    def rm_stop_drag_teach(self):
        self.calls.append(("stop_drag", None, {}))
        return 0

    def rm_save_trajectory(self, path):
        self.calls.append(("save_trajectory", path, {}))
        return 0, 12

    def rm_set_arm_slow_stop(self):
        self.calls.append(("quick_stop", None, {}))
        return self.quick_stop_code

    def rm_set_arm_stop(self):
        self.calls.append(("emergency_stop", None, {}))
        return self.emergency_stop_code


class _FakeArmBackend:
    def __init__(self) -> None:
        self.robot = _FakeSdkRobot()
        self.is_connected = True
        self.sdk_lock = RLock()


class _FakeRealManController:
    def __init__(self) -> None:
        self.robot1_ctrl = _FakeArmBackend()
        self.robot2_ctrl = _FakeArmBackend()
        self.closed = False

    def demo_send_project(self, *_args, **_kwargs):
        return True

    def demo_get_program_run_state(self, *_args, **_kwargs):
        return True

    def shutdown(self):
        self.closed = True


class _FailIfEnteredLock:
    def __enter__(self):
        raise AssertionError("safety stop acquired the motion SDK lock")

    def __exit__(self, *_args):
        return False


def _pose(seed: float) -> CartesianPose:
    return CartesianPose.from_iterable(
        (seed, seed + 1, seed + 2, seed + 3, seed + 4, seed + 5)
    )


def _tool_rack_options(
    arm: ArmId = ArmId.RIGHT,
) -> RealManToolRackOptions:
    return RealManToolRackOptions(
        arm=arm,
        slots=(
            RealManToolRackSlot(
                slot_id=1,
                approach_pose=_pose(10),
                attach_pose=_pose(20),
                detach_pose=_pose(30),
                attach_dwell_seconds=0,
                detach_dwell_seconds=0,
            ),
            RealManToolRackSlot(
                slot_id=2,
                approach_pose=_pose(40),
                attach_pose=_pose(50),
                detach_pose=_pose(60),
                attach_dwell_seconds=0,
                detach_dwell_seconds=0,
            ),
        ),
    )


def _provider_config(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "ROBOT_PROVIDER": "realman",
        "ROBOT_MODEL": "rm75-dual",
        "ROBOT1_IP": "192.0.2.1",
        "ROBOT1_PORT": 8080,
        "ROBOT1_INITIAL_POSE": _pose(1).to_list(),
        "ROBOT2_IP": "192.0.2.2",
        "ROBOT2_PORT": 8080,
        "ROBOT2_INITIAL_POSE": _pose(2).to_list(),
        "MOVE_VELOCITY": 20,
        "MOVE_RADIUS": 0,
        "MOVE_CONNECT": 0,
        "MOVE_BLOCK": 1,
        "GRIPPER_PICK_SPEED": 200,
        "GRIPPER_PICK_FORCE": 1000,
        "GRIPPER_PICK_TIMEOUT": 3,
        "GRIPPER_RELEASE_SPEED": 100,
        "GRIPPER_RELEASE_TIMEOUT": 3,
        "MAX_ATTEMPTS": 5,
        "ROBOT_TOOL_RACK_ARM": "right",
    }
    for slot_id in (1, 2):
        values.update({
            f"ROBOT_TOOL_RACK_SLOT_{slot_id}_APPROACH_POSE": (
                _pose(slot_id * 10).to_list()
            ),
            f"ROBOT_TOOL_RACK_SLOT_{slot_id}_ATTACH_POSE": (
                _pose(slot_id * 10 + 1).to_list()
            ),
            f"ROBOT_TOOL_RACK_SLOT_{slot_id}_DETACH_POSE": (
                _pose(slot_id * 10 + 2).to_list()
            ),
            f"ROBOT_TOOL_RACK_SLOT_{slot_id}_ATTACH_DWELL_SECONDS": 0,
            f"ROBOT_TOOL_RACK_SLOT_{slot_id}_DETACH_DWELL_SECONDS": 0,
        })
    values.update(overrides)
    return SimpleNamespace(**values)


class RobotModelTests(unittest.TestCase):
    def test_models_validate_units_and_ranges(self):
        self.assertEqual(ArmId.LEFT, ArmId.parse("robot1"))
        self.assertEqual(ArmId.RIGHT, ArmId.parse("right"))
        self.assertEqual(MotionMode.JOINT, MotionMode.parse("move_j"))
        self.assertEqual(
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            CartesianPose.from_iterable(range(1, 7)).to_list(),
        )
        self.assertEqual(
            [1.0, 2.0],
            JointVector.from_iterable((1, 2)).to_list(),
        )

        with self.assertRaises(ValueError):
            CartesianPose.from_iterable((1, 2, 3))
        with self.assertRaises(ValueError):
            MotionOptions(velocity_percent=0)
        with self.assertRaises(ValueError):
            JointVector.from_iterable(())
        with self.assertRaises(ValueError):
            CartesianPose.from_iterable((1, 2, 3, 4, 5, math.nan))


class RealManRobotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = _FakeRealManController()
        self.adapter = RealManRobotAdapter(
            self.controller,
            default_motion=MotionOptions(velocity_percent=20),
            tool_rack_options=_tool_rack_options(),
        )

    def test_adapter_implements_vendor_neutral_contract(self):
        self.assertIsInstance(self.adapter, RobotSystem)
        self.assertIsInstance(self.adapter, ArmTelemetryReader)
        pose = CartesianPose.from_iterable((1, 2, 3, 4, 5, 6))
        self.adapter.move_to_pose(
            ArmId.LEFT,
            pose,
            MotionMode.LINEAR,
            MotionOptions(
                velocity_percent=30,
                blend_radius=2,
                connected=True,
                blocking=False,
            ),
        )

        call = self.controller.robot1_ctrl.robot.calls[-1]
        self.assertEqual("movel", call[0])
        self.assertEqual(pose.to_list(), call[1])
        self.assertEqual(
            {"v": 30, "r": 2, "connect": 1, "block": 0},
            call[2],
        )

        state = self.adapter.read_arm_state(ArmId.RIGHT)
        self.assertEqual(ArmId.RIGHT, state.arm)
        self.assertEqual([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], state.pose.to_list())
        self.assertEqual([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], state.joints.to_list())

        self.adapter.open_gripper(ArmId.LEFT)
        self.assertEqual(
            "gripper_release",
            self.controller.robot1_ctrl.robot.calls[-1][0],
        )
        self.adapter.follow_joints(
            ArmId.RIGHT,
            JointVector.from_iterable((1, 2, 3, 4, 5, 6)),
            follow=True,
            trajectory_mode=1,
        )
        self.assertEqual(
            "movej_canfd",
            self.controller.robot2_ctrl.robot.calls[-1][0],
        )
        self.adapter.change_tool(
            1,
            attach=False,
            eject_tool=lambda: True,
        )
        tool_calls = self.controller.robot2_ctrl.robot.calls[-3:]
        self.assertEqual(
            ["movel", "movel", "movel"],
            [call[0] for call in tool_calls],
        )
        self.assertEqual(_pose(10).to_list(), tool_calls[0][1])
        self.assertEqual(_pose(30).to_list(), tool_calls[1][1])
        self.assertEqual(_pose(10).to_list(), tool_calls[2][1])

    def test_adapter_reports_real_telemetry_and_derives_velocity(self):
        with (
            patch(
                "src.device_runtime.adapters.time.monotonic_ns",
                side_effect=(1_000_000_000, 2_000_000_000),
            ),
            patch(
                "src.device_runtime.adapters.time.time_ns",
                side_effect=(10_000_000_000, 11_000_000_000),
            ),
        ):
            first = self.adapter.read_arm_telemetry(ArmId.LEFT)
            self.controller.robot1_ctrl.robot.state["joint"] = [
                value + 1 for value in range(1, 7)
            ]
            second = self.adapter.read_arm_telemetry(ArmId.LEFT)

        self.assertIsNone(first.joint_velocities_deg_s)
        self.assertEqual((1.0,) * 6, second.joint_velocities_deg_s)
        self.assertEqual((0.1, 0.2, 0.3, 0.4, 0.5, 0.6), first.joint_currents_amperes)
        self.assertEqual((1.0, 2.0, 3.0, 4.0, 5.0, 6.0), first.end_effector_wrench)
        self.assertAlmostEqual(0.25, first.gripper.position_normalized)
        self.assertAlmostEqual(4.903325, first.gripper.force_newtons)

    def test_tool_rack_uses_configured_arm_and_slot_poses(self):
        adapter = RealManRobotAdapter(
            self.controller,
            default_motion=MotionOptions(
                velocity_percent=25,
                blend_radius=3,
                connected=True,
                blocking=False,
            ),
            tool_rack_options=_tool_rack_options(ArmId.LEFT),
        )

        adapter.change_tool(2, attach=True)

        calls = self.controller.robot1_ctrl.robot.calls
        self.assertEqual(
            [_pose(40).to_list(), _pose(50).to_list(), _pose(40).to_list()],
            [call[1] for call in calls],
        )
        self.assertTrue(all(call[0] == "movel" for call in calls))
        self.assertTrue(all(
            call[2] == {"v": 25, "r": 3, "connect": 1, "block": 0}
            for call in calls
        ))
        self.assertEqual([], self.controller.robot2_ctrl.robot.calls)

    def test_tool_detach_reports_ejector_failure(self):
        adapter = RealManRobotAdapter(
            self.controller,
            default_motion=MotionOptions(),
            tool_rack_options=_tool_rack_options(),
        )

        with self.assertRaisesRegex(
            RobotOperationError,
            "tool ejector reported failure",
        ):
            adapter.change_tool(
                1,
                attach=False,
                eject_tool=lambda: False,
            )

    def test_adapter_normalizes_errors_and_trajectory_results(self):
        self.controller.robot1_ctrl.robot.state_code = 7
        with self.assertRaises(RobotOperationError) as raised:
            self.adapter.read_arm_state(ArmId.LEFT)
        self.assertEqual(7, raised.exception.code)

        result = self.adapter.save_trajectory(ArmId.RIGHT, "demo.txt")
        self.assertEqual(Path("demo.txt"), result.path)
        self.assertEqual(12, result.point_count)

        self.adapter.close()
        self.assertTrue(self.controller.closed)

    def test_stop_interrupts_both_arms_without_motion_sdk_locks(self):
        self.controller.robot1_ctrl.sdk_lock = _FailIfEnteredLock()
        self.controller.robot2_ctrl.sdk_lock = _FailIfEnteredLock()

        self.adapter.stop(StopMode.QUICK)
        self.adapter.stop(StopMode.EMERGENCY)

        for backend in (
            self.controller.robot1_ctrl,
            self.controller.robot2_ctrl,
        ):
            calls = [call[0] for call in backend.robot.calls]
            self.assertIn("quick_stop", calls)
            self.assertIn("emergency_stop", calls)

    def test_stop_attempts_both_arms_before_reporting_failure(self):
        self.controller.robot1_ctrl.robot.quick_stop_code = 9

        with self.assertRaisesRegex(RuntimeError, "left: SDK code 9"):
            self.adapter.stop(StopMode.QUICK)

        self.assertEqual(
            "quick_stop",
            self.controller.robot2_ctrl.robot.calls[-1][0],
        )


class RobotProviderTests(unittest.TestCase):
    def test_unknown_provider_fails_explicitly(self):
        settings = replace(
            ApplicationSettings.defaults(),
            robot=RobotSettings(robot_provider="unknown"),
        )
        with self.assertRaisesRegex(
            DeviceInitializationError,
            "unsupported robot provider",
        ):
            create_device_runtime(settings, simulation=False)

    def test_realman_provider_declares_core_and_optional_capabilities(self):
        settings = ApplicationSettings.from_config(_provider_config()).robot
        provider = resolve_robot_provider(settings)

        self.assertEqual("realman", provider.name)
        self.assertTrue({
            DeviceCapability.MOTION,
            DeviceCapability.ARM_MOTION,
            DeviceCapability.ARM_STATE,
            DeviceCapability.ARM_TELEMETRY,
            DeviceCapability.GRIPPER,
            DeviceCapability.TOOL_RACK,
        }.issubset(provider.capabilities))

    def test_realman_settings_validate_model_specific_configuration(self):
        settings = RealManProviderSettings.from_settings(
            ApplicationSettings.from_config(_provider_config()).robot
        )

        self.assertFalse(settings.motion.connected)
        self.assertTrue(settings.motion.blocking)
        self.assertEqual(ArmId.RIGHT, settings.tool_rack.arm)
        self.assertEqual(2, len(settings.tool_rack.slots))

        with self.assertRaisesRegex(
            DeviceInitializationError,
            "MOVE_CONNECT must be 0 or 1",
        ):
            RealManProviderSettings.from_settings(
                ApplicationSettings.from_config(
                    _provider_config(MOVE_CONNECT="sometimes")
                ).robot
            )

        with self.assertRaisesRegex(
            DeviceInitializationError,
            "cartesian pose requires 6 values",
        ):
            RealManProviderSettings.from_settings(
                ApplicationSettings.from_config(
                    _provider_config(ROBOT1_INITIAL_POSE=[1, 2])
                ).robot
            )


class RobotProviderContractTests(unittest.TestCase):
    def test_core_contract_is_shared_by_realman_and_simulation(self):
        controller = _FakeRealManController()
        implementations: tuple[RobotSystem, ...] = (
            RealManRobotAdapter(
                controller,
                default_motion=MotionOptions(),
                tool_rack_options=_tool_rack_options(),
            ),
            SimulatedRobotSystem(),
        )

        for robot in implementations:
            with self.subTest(provider=type(robot).__name__):
                self._assert_core_contract(robot)
            robot.close()

    def _assert_core_contract(self, robot: RobotSystem) -> None:
        self.assertIsInstance(robot, RobotSystem)
        target = _pose(70)
        for arm in ArmId:
            robot.move_to_pose(
                arm,
                target,
                MotionMode.LINEAR,
                MotionOptions(velocity_percent=15),
            )
            self.assertEqual(arm, robot.read_arm_state(arm).arm)
            robot.open_gripper(arm)
            robot.close_gripper(arm)
            robot.move_gripper(arm, 500)


class RobotApplicationServiceTests(unittest.TestCase):
    def test_manual_control_and_query_use_normalized_robot_contract(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.manual_control.set_gripper("left", position=350)
        robot = services.device_runtime.require(ROBOT_SYSTEM, RobotSystem)
        self.assertEqual(350, robot.gripper_positions[ArmId.LEFT])

        state = services.robot_query.read_state("robot1")
        self.assertEqual(ArmId.LEFT, state.arm)

    def test_drag_teaching_owns_robot_until_trajectory_is_saved(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="move",
                name="move",
                type=ActionType.MOVE,
                parameters={
                    "目标": "机械臂",
                    "臂": "右",
                    "模式": "move_j",
                    "点位": [0, 0, 0, 0, 0, 0],
                },
            )
        )

        services.trajectory_teaching.start("right")
        with self.assertRaises(ResourceBusyError):
            services.execution.start([item], origin="test")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.txt"
            result = services.trajectory_teaching.stop_and_save(str(path))
            self.assertEqual(path, result.path)
            self.assertTrue(path.is_file())

        final = services.execution.start([item], origin="test").wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)


if __name__ == "__main__":
    unittest.main()
