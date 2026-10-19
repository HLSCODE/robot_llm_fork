from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from src.application.teaching import TeachingService, offset_pose
from src.devices import ResourceArbiter
from src.devices.runtime.arm_models import ArmId, CartesianPose, MotionMode
from src.devices.runtime.fakes import SimulatedRobotSystem
from src.devices.runtime.ids import ROBOT_SYSTEM
from src.devices.runtime.models import ResourceBusyError, StopMode
from src.geometry.pose_compensation import euler_xyz_to_matrix, matmul


def service_for(robot):
    resources = ResourceArbiter()
    runtime = SimpleNamespace(get_if_ready=lambda _id: robot)
    return TeachingService(runtime, resources), resources


def test_session_requires_ready_robot_and_releases_failed_reservation():
    service, resources = service_for(None)
    with pytest.raises(RuntimeError, match="未就绪"):
        service.begin(ArmId.LEFT)
    assert resources.owner_of(ROBOT_SYSTEM) is None


def test_session_excludes_other_control_and_close_releases():
    robot = SimulatedRobotSystem()
    service, resources = service_for(robot)
    service.begin(ArmId.LEFT)
    with pytest.raises(ResourceBusyError):
        resources.acquire("workflow", (ROBOT_SYSTEM,))
    service.close()
    assert robot.last_stop_mode is StopMode.QUICK
    assert resources.owner_of(ROBOT_SYSTEM) is None
    service.close()


def test_busy_resource_does_not_stop_another_owner():
    robot = SimulatedRobotSystem()
    service, resources = service_for(robot)
    with resources.acquire("workflow", (ROBOT_SYSTEM,)):
        with pytest.raises(ResourceBusyError):
            service.begin(ArmId.RIGHT)
        service.close()
        assert robot.last_stop_mode is None


def test_steps_use_actual_state_and_selected_arm():
    robot = SimulatedRobotSystem()
    service, _ = service_for(robot)
    service.begin(ArmId.RIGHT)
    service.step(joint=False, axis=0, amount=1, speed=5)
    service.step(joint=False, axis=0, amount=1, speed=5)
    service.step(joint=True, axis=6, amount=-1, speed=5)
    assert robot.states[ArmId.RIGHT].pose.x_m == pytest.approx(.002)
    assert robot.states[ArmId.LEFT].pose.x_m == 0
    assert robot.states[ArmId.RIGHT].joints.positions_deg[6] == -1
    service.close()


@pytest.mark.parametrize("joint,axis,amount,speed", [
    (False, 0, 11, 5), (False, 3, 6, 5), (True, 7, 1, 5),
    (True, 0, 6, 5), (False, 0, float("nan"), 5), (False, 0, 1, 11),
])
def test_rejects_invalid_commands(joint, axis, amount, speed):
    robot = SimulatedRobotSystem()
    service, _ = service_for(robot)
    service.begin(ArmId.LEFT)
    with pytest.raises(ValueError):
        service.step(joint=joint, axis=axis, amount=amount, speed=speed)
    assert robot.states[ArmId.LEFT].pose.x_m == 0
    service.close()


def test_fixed_axis_rotation_uses_matrix_composition():
    pose = CartesianPose(1, 2, 3, .3, .4, .5)
    actual = offset_pose(pose, 3, 1)
    expected = matmul(euler_xyz_to_matrix(math.radians(1), 0, 0),
                      euler_xyz_to_matrix(.3, .4, .5))
    for row, expected_row in zip(euler_xyz_to_matrix(*actual.to_list()[3:]), expected):
        assert row == pytest.approx(expected_row)
    assert actual.to_list()[:3] == [1, 2, 3]


def test_cancelled_pending_step_never_moves():
    robot = SimulatedRobotSystem()
    service, _ = service_for(robot)
    service.begin(ArmId.LEFT)
    service.request_stop()
    with pytest.raises(RuntimeError, match="停止"):
        service.step(joint=False, axis=0, amount=1, speed=5)
    service.close()
    assert robot.states[ArmId.LEFT].pose.x_m == 0


def test_stop_keeps_lease_until_blocking_command_returns():
    entered, released = Event(), Event()

    class BlockingRobot(SimulatedRobotSystem):
        def move_to_pose(self, arm, pose, mode, options=None):
            assert mode is MotionMode.LINEAR
            assert options.blocking and options.velocity_percent == 5
            entered.set()
            assert released.wait(2)

        def stop(self, mode):
            assert resources.owner_of(ROBOT_SYSTEM) is not None
            released.set()
            super().stop(mode)

    robot = BlockingRobot()
    service, resources = service_for(robot)
    service.begin(ArmId.LEFT)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(service.step, joint=False, axis=0, amount=1, speed=5)
        assert entered.wait(2)
        service.close()
        result.result(timeout=2)
    assert resources.owner_of(ROBOT_SYSTEM) is None


def test_stop_failure_retains_lease_and_disables_motion():
    class FailedStop(SimulatedRobotSystem):
        def stop(self, mode):
            raise RuntimeError("stop rejected")

    service, resources = service_for(FailedStop())
    service.begin(ArmId.LEFT)
    with pytest.raises(RuntimeError, match="stop rejected"):
        service.close()
    assert service.active and not service.enabled
    assert resources.owner_of(ROBOT_SYSTEM) is not None


def test_joint_controls_require_vendor_capability():
    robot = SimulatedRobotSystem()
    pose_only = SimpleNamespace(
        read_arm_state=robot.read_arm_state, try_read_arm_state=robot.try_read_arm_state,
        move_to_pose=robot.move_to_pose, supported_stop_modes=robot.supported_stop_modes,
        stop=robot.stop,
    )
    service, _ = service_for(pose_only)
    service.begin(ArmId.LEFT)
    assert not service.supports_joints
    with pytest.raises(RuntimeError, match="不支持"):
        service.step(joint=True, axis=0, amount=1, speed=5)
    service.close()


def test_cancellation_during_target_read_prevents_dispatch():
    entered, released = Event(), Event()

    class SlowReader(SimulatedRobotSystem):
        moves = 0

        def read_arm_state(self, arm):
            entered.set()
            assert released.wait(2)
            return super().read_arm_state(arm)

        def move_to_pose(self, *args, **kwargs):
            self.moves += 1

        def stop(self, mode):
            released.set()
            super().stop(mode)

    robot = SlowReader()
    service, _ = service_for(robot)
    service.begin(ArmId.LEFT)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(service.step, joint=False, axis=0, amount=1, speed=5)
        assert entered.wait(2)
        service.close()
        with pytest.raises(RuntimeError, match="停止"):
            result.result(timeout=2)
    assert robot.moves == 0
