from __future__ import annotations

import unittest

from src.application import create_application_services
from src.domain.execution_context import ExecutionContext
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.configuration.settings import ApplicationSettings, VisionSettings
from src.devices import (
    ArmId,
    ArmState,
    CartesianPose,
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
    MotionMode,
)
from src.devices.runtime.ids import BODY_AXIS, MOBILE_BASE, ROBOT_SYSTEM
from src.execution import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionResultCode,
    ExecutionControl,
    ExecutionState,
)
from src.execution.handlers import (
    BaseMoveActionHandler,
    BodyMoveActionHandler,
    MotionHandlerOptions,
    MoveActionHandler,
    RobotMoveActionHandler,
)


class _RecordingArmMotion:
    def __init__(self, failures_before_success: int = 0) -> None:
        self.failures_before_success = failures_before_success
        self.calls: list[tuple[ArmId, CartesianPose, MotionMode]] = []

    def move_to_pose(
        self,
        arm: ArmId,
        pose: CartesianPose,
        mode: MotionMode,
        _options=None,
    ) -> None:
        self.calls.append((arm, pose, mode))
        if len(self.calls) <= self.failures_before_success:
            raise RuntimeError("transient motion failure")

    def read_arm_state(self, arm: ArmId) -> ArmState:
        return ArmState(
            arm=arm,
            pose=CartesianPose(0.1, 0.2, 0.3, 1.0, 2.0, 3.0),
        )

    def try_read_arm_state(self, arm: ArmId) -> ArmState:
        return self.read_arm_state(arm)


class _BodyAxis:
    def __init__(
        self,
        reached_values: list[bool | None],
        *,
        control_to_cancel: ExecutionControl | None = None,
    ) -> None:
        self.positions: list[int] = []
        self._reached_values = reached_values
        self._control_to_cancel = control_to_cancel

    def move_to(self, position: int) -> None:
        self.positions.append(position)

    def is_reached(self) -> bool | None:
        if self._control_to_cancel is not None:
            self._control_to_cancel.cancel()
        if self._reached_values:
            return self._reached_values.pop(0)
        return False

    def emergency_stop(self) -> None:
        return None

    def close(self) -> None:
        return None


class _MobileBase:
    def __init__(self) -> None:
        self.position_calls: list[tuple[int, int]] = []
        self.distance_calls: list[tuple[float, float, float]] = []

    def move_to_position(
        self,
        location_id: int,
        coordinate_id: int,
    ) -> bool:
        self.position_calls.append((location_id, coordinate_id))
        return True

    def move_slowly(
        self,
        x: float,
        y: float,
        angle: float,
    ) -> bool:
        self.distance_calls.append((x, y, angle))
        return True

    def close(self) -> None:
        return None


def _runtime_with(
    device_id: str,
    capability: DeviceCapability,
    device,
) -> DeviceRuntime:
    runtime = DeviceRuntime()
    runtime.register(
        DeviceRegistration(
            device_id=device_id,
            capabilities=frozenset(
                {DeviceCapability.MOTION, capability}
            ),
            factory=lambda: device,
            close=lambda value: value.close()
            if hasattr(value, "close")
            else None,
        )
    )
    return runtime


def _action_context(
    control: ExecutionControl | None = None,
) -> tuple[ActionExecutionContext, list[tuple[str, str]]]:
    logs: list[tuple[str, str]] = []
    context = ActionExecutionContext(
        action_name="motion test",
        control=control or ExecutionControl(),
        timeout_seconds=1.0,
        log=lambda message, level: logs.append((message, level)),
    )
    return context, logs


class RobotMoveActionHandlerTests(unittest.TestCase):
    def test_relative_motion_reads_current_pose_and_preserves_orientation(self):
        robot = _RecordingArmMotion()
        runtime = DeviceRuntime()
        runtime.register(DeviceRegistration(
            device_id=ROBOT_SYSTEM,
            capabilities=frozenset({
                DeviceCapability.MOTION,
                DeviceCapability.ARM_MOTION,
                DeviceCapability.ARM_STATE,
            }),
            factory=lambda: robot,
            close=lambda _value: None,
        ))
        handler = RobotMoveActionHandler(
            runtime,
            ExecutionContext(),
            MotionHandlerOptions(),
            VisionSettings(),
            lambda **_kwargs: None,
        )
        context, _logs = _action_context()

        result = handler(
            {
                "目标": "机械臂相对",
                "臂": "右",
                "坐标系": "base",
                "模式": "move_l",
                "x_mm": 10,
                "y_mm": -20,
                "z_mm": 5,
            },
            context,
        )

        self.assertTrue(result.successful)
        arm, pose, mode = robot.calls[-1]
        self.assertEqual(ArmId.RIGHT, arm)
        self.assertEqual(MotionMode.LINEAR, mode)
        self.assertAlmostEqual(0.11, pose.x_m)
        self.assertAlmostEqual(0.18, pose.y_m)
        self.assertAlmostEqual(0.305, pose.z_m)
        self.assertEqual((1.0, 2.0, 3.0), (
            pose.rx_rad,
            pose.ry_rad,
            pose.rz_rad,
        ))

    def test_arm_motion_retries_are_bounded_and_vendor_neutral(self):
        arm_motion = _RecordingArmMotion(failures_before_success=2)
        runtime = _runtime_with(
            ROBOT_SYSTEM,
            DeviceCapability.ARM_MOTION,
            arm_motion,
        )
        options = MotionHandlerOptions(
            arm_move_max_attempts=3,
            arm_move_retry_delay_seconds=0,
        )
        handler = RobotMoveActionHandler(
            runtime,
            ExecutionContext(),
            options,
            VisionSettings(),
            lambda **_kwargs: None,
        )
        context, logs = _action_context()

        result = handler(
            {
                "臂": "左",
                "模式": "move_j",
                "点位": [0, 0.1, 0.2, 0, 0, 0],
            },
            context,
        )

        self.assertTrue(result.successful)
        self.assertEqual(3, len(arm_motion.calls))
        arm, pose, mode = arm_motion.calls[-1]
        self.assertEqual(ArmId.LEFT, arm)
        self.assertEqual(MotionMode.JOINT, mode)
        self.assertEqual(0.2, pose.z_m)
        self.assertIn(("机械臂移动执行完成", "info"), logs)

    def test_invalid_arm_parameters_fail_before_device_motion(self):
        arm_motion = _RecordingArmMotion()
        runtime = _runtime_with(
            ROBOT_SYSTEM,
            DeviceCapability.ARM_MOTION,
            arm_motion,
        )
        handler = RobotMoveActionHandler(
            runtime,
            ExecutionContext(),
            MotionHandlerOptions(),
            VisionSettings(),
            lambda **_kwargs: None,
        )
        context, logs = _action_context()

        result = handler(
            {"臂": "unknown", "模式": "move_j", "点位": [0] * 6},
            context,
        )

        self.assertFalse(result.successful)
        self.assertEqual(ActionResultCode.INVALID_PARAMETERS, result.code)
        self.assertEqual("robot_system.move_to_pose", result.operation)
        self.assertEqual(ROBOT_SYSTEM, result.device_id)
        self.assertEqual([], arm_motion.calls)
        self.assertEqual("error", logs[-1][1])


class BodyMoveActionHandlerTests(unittest.TestCase):
    def test_body_move_polls_until_reached(self):
        body = _BodyAxis([False, True])
        runtime = _runtime_with(
            BODY_AXIS,
            DeviceCapability.BODY_AXIS,
            body,
        )
        runtime.initialize(BODY_AXIS)
        handler = BodyMoveActionHandler(
            runtime,
            MotionHandlerOptions(body_poll_interval_seconds=0.001),
        )
        context, _logs = _action_context()

        self.assertTrue(handler({"位置": "42"}, context).successful)
        self.assertEqual([42], body.positions)

    def test_body_move_does_not_swallow_cancellation(self):
        control = ExecutionControl()
        body = _BodyAxis([False], control_to_cancel=control)
        runtime = _runtime_with(
            BODY_AXIS,
            DeviceCapability.BODY_AXIS,
            body,
        )
        runtime.initialize(BODY_AXIS)
        handler = BodyMoveActionHandler(
            runtime,
            MotionHandlerOptions(body_poll_interval_seconds=0.001),
        )
        context, _logs = _action_context(control)

        with self.assertRaises(ActionCancelledError):
            handler({"位置": 10}, context)


class BaseMoveActionHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = _MobileBase()
        self.runtime = _runtime_with(
            MOBILE_BASE,
            DeviceCapability.MOBILE_BASE,
            self.base,
        )
        self.runtime.initialize(MOBILE_BASE)
        self.handler = BaseMoveActionHandler(self.runtime)

    def test_base_modes_normalize_parameters_at_handler_boundary(self):
        context, _logs = _action_context()
        self.assertTrue(
            self.handler(
                {"move_mode": "position", "id": "2", "cid": "3"},
                context,
            ).successful
        )
        self.assertTrue(
            self.handler(
                {
                    "move_mode": "distance",
                    "x": "1.5",
                    "y": 2,
                    "angle": "-3.5",
                },
                context,
            ).successful
        )

        self.assertEqual([(2, 3)], self.base.position_calls)
        self.assertEqual(
            [(1.5, 2.0, -3.5)],
            self.base.distance_calls,
        )

    def test_unknown_move_target_and_base_mode_fail_explicitly(self):
        context, logs = _action_context()
        move_handler = MoveActionHandler(
            RobotMoveActionHandler(
                DeviceRuntime(),
                ExecutionContext(),
                MotionHandlerOptions(),
                VisionSettings(),
                lambda **_kwargs: None,
            ),
            BodyMoveActionHandler(
                DeviceRuntime(),
                MotionHandlerOptions(),
            ),
        )

        self.assertFalse(
            move_handler({"目标": "头部"}, context).successful
        )
        self.assertFalse(
            self.handler(
                {"move_mode": "teleport"},
                context,
            ).successful
        )
        self.assertEqual(
            ["未知的移动目标: 头部", "未知的移动方式: teleport"],
            [message for message, level in logs if level == "error"],
        )


class MotionHandlerIntegrationTests(unittest.TestCase):
    def test_all_motion_action_routes_use_the_unified_registry(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.devices.initialize_many((BODY_AXIS, MOBILE_BASE))
        definitions = (
            ActionDefinition(
                id="arm",
                name="arm move",
                type=ActionType.MOVE,
                parameters={
                    "目标": "机械臂",
                    "臂": "左",
                    "模式": "move_j",
                    "点位": [0, 0, 0, 0, 0, 0],
                },
            ),
            ActionDefinition(
                id="body",
                name="body move",
                type=ActionType.MOVE,
                parameters={"目标": "身体", "位置": 20},
            ),
            ActionDefinition(
                id="base",
                name="base move",
                type=ActionType.BASE_MOVE,
                parameters={
                    "move_mode": "distance",
                    "x": 1,
                    "y": 2,
                    "angle": 3,
                },
            ),
        )
        sequence = [
            SequenceItem.from_definition(definition)
            for definition in definitions
        ]

        final = services.execution.start(
            sequence,
            origin="test",
        ).wait(1)

        self.assertEqual(ExecutionState.SUCCEEDED, final.state)
        self.assertFalse(services.devices.shutdown_all())


if __name__ == "__main__":
    unittest.main()
