from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from threading import RLock

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.device_runtime import (
    ArmId,
    CartesianPose,
    DeviceInitializationError,
    JointVector,
    MotionMode,
    MotionOptions,
    ResourceBusyError,
    RobotOperationError,
    RobotSystem,
)
from src.device_runtime.adapters import RealManRobotAdapter
from src.device_runtime.factory import create_device_runtime
from src.device_runtime.ids import ROBOT_SYSTEM
from src.execution import ExecutionState


class _FakeSdkRobot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, object]]] = []
        self.state_code = 0
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

    def pick_gun1(self):
        return True

    def pick_gun2(self):
        return True

    def drop_gun1(self, eject_tip):
        return eject_tip()

    def drop_gun2(self, eject_tip):
        return eject_tip()

    def shutdown(self):
        self.closed = True


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
            eject_tool=lambda: True,
        )

    def test_adapter_implements_vendor_neutral_contract(self):
        self.assertIsInstance(self.adapter, RobotSystem)
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
        self.adapter.change_tool(1, attach=False)

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


class RobotProviderTests(unittest.TestCase):
    def test_unknown_provider_fails_explicitly(self):
        config = type("Config", (), {"ROBOT_PROVIDER": "unknown"})()
        runtime = create_device_runtime(config, simulation=False)
        with self.assertRaisesRegex(
            DeviceInitializationError,
            "unsupported robot provider",
        ):
            runtime.initialize(ROBOT_SYSTEM)


class RobotApplicationServiceTests(unittest.TestCase):
    def test_manual_control_and_query_use_normalized_robot_contract(self):
        services = create_application_services(object(), simulation=True)
        services.manual_control.set_gripper("left", position=350)
        robot = services.device_runtime.require(ROBOT_SYSTEM, RobotSystem)
        self.assertEqual(350, robot.gripper_positions[ArmId.LEFT])

        state = services.robot_query.read_state("robot1")
        self.assertEqual(ArmId.LEFT, state.arm)

    def test_drag_teaching_owns_robot_until_trajectory_is_saved(self):
        services = create_application_services(object(), simulation=True)
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 0.01},
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
