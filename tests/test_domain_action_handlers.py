from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from src.application import create_application_services
from src.core.execution_context import ExecutionContext
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.core.settings import (
    ApplicationSettings,
    ExecutionSettings,
)
from src.device_runtime import (
    ArmId,
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
    DeviceState,
    RobotSystem,
)
from src.device_runtime.factory import create_device_runtime
from src.device_runtime.ids import CAMERA, ROBOT_SYSTEM
from src.execution import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionResultCode,
    ExecutionControl,
    ExecutionState,
)
from src.execution.handlers import (
    ChangeToolActionHandler,
    TrajectoryActionHandler,
    TrajectoryHandlerOptions,
    VisionCaptureActionHandler,
    VisionRelocalizationActionHandler,
)


class _TrajectoryRobot:
    def __init__(
        self,
        completion_values: list[bool],
        control_to_cancel: ExecutionControl | None = None,
    ) -> None:
        self._completion_values = completion_values
        self._control_to_cancel = control_to_cancel
        self.sent: list[tuple[ArmId, Path]] = []

    def start_drag_teaching(self, _arm: ArmId) -> None:
        return None

    def stop_drag_teaching(self, _arm: ArmId) -> None:
        return None

    def save_trajectory(self, _arm: ArmId, _path: str | Path):
        raise NotImplementedError

    def send_trajectory(self, arm: ArmId, path: str | Path) -> None:
        self.sent.append((arm, Path(path)))

    def is_trajectory_complete(self, _arm: ArmId) -> bool:
        if self._control_to_cancel is not None:
            self._control_to_cancel.cancel()
        if self._completion_values:
            return self._completion_values.pop(0)
        return False


class _ToolRack:
    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    def change_tool(self, slot: int, *, attach: bool) -> None:
        self.calls.append((slot, attach))


def _runtime_with_robot(
    robot,
    capability: DeviceCapability,
) -> DeviceRuntime:
    runtime = DeviceRuntime()
    runtime.register(
        DeviceRegistration(
            device_id=ROBOT_SYSTEM,
            capabilities=frozenset({capability}),
            factory=lambda: robot,
            close=lambda _device: None,
        )
    )
    return runtime


def _action_context(
    control: ExecutionControl | None = None,
) -> tuple[ActionExecutionContext, list[tuple[str, str]]]:
    logs: list[tuple[str, str]] = []
    context = ActionExecutionContext(
        action_name="domain handler test",
        control=control or ExecutionControl(),
        timeout_seconds=1.0,
        log=lambda message, level: logs.append((message, level)),
    )
    return context, logs


class TrajectoryActionHandlerTests(unittest.TestCase):
    def test_trajectory_is_sent_and_polled_through_capability(self):
        robot = _TrajectoryRobot([False, True])
        runtime = _runtime_with_robot(
            robot,
            DeviceCapability.TRAJECTORY,
        )
        handler = TrajectoryActionHandler(
            runtime,
            TrajectoryHandlerOptions(poll_interval_seconds=0.001),
        )
        context, logs = _action_context()

        with TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.txt"
            path.write_text("trajectory", encoding="utf-8")

            result = handler(
                {"robot": "robot2", "file_path": str(path)},
                context,
            )

            self.assertTrue(result.successful)
            self.assertEqual([(ArmId.RIGHT, path)], robot.sent)
            self.assertIn(("轨迹执行完成", "info"), logs)

    def test_invalid_file_does_not_initialize_robot(self):
        created = 0

        def factory() -> _TrajectoryRobot:
            nonlocal created
            created += 1
            return _TrajectoryRobot([True])

        runtime = DeviceRuntime()
        runtime.register(
            DeviceRegistration(
                device_id=ROBOT_SYSTEM,
                capabilities=frozenset({DeviceCapability.TRAJECTORY}),
                factory=factory,
                close=lambda _device: None,
            )
        )
        context, _logs = _action_context()
        handler = TrajectoryActionHandler(
            runtime,
            TrajectoryHandlerOptions(),
        )

        result = handler(
            {"file_path": "missing-trajectory.txt"},
            context,
        )
        self.assertFalse(result.successful)
        self.assertEqual(ActionResultCode.RESOURCE_NOT_FOUND, result.code)
        self.assertEqual("trajectory.load_file", result.operation)
        self.assertEqual(ROBOT_SYSTEM, result.device_id)
        self.assertEqual(0, created)
        self.assertEqual(
            DeviceState.REGISTERED,
            runtime.snapshot(ROBOT_SYSTEM).state,
        )

    def test_trajectory_polling_does_not_swallow_cancellation(self):
        control = ExecutionControl()
        robot = _TrajectoryRobot([False], control_to_cancel=control)
        runtime = _runtime_with_robot(
            robot,
            DeviceCapability.TRAJECTORY,
        )
        handler = TrajectoryActionHandler(
            runtime,
            TrajectoryHandlerOptions(poll_interval_seconds=0.001),
        )
        context, _logs = _action_context(control)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.txt"
            path.write_text("trajectory", encoding="utf-8")

            with self.assertRaises(ActionCancelledError):
                handler({"file_path": path}, context)


class ChangeToolActionHandlerTests(unittest.TestCase):
    def test_change_tool_normalizes_slot_before_device_call(self):
        tool_rack = _ToolRack()
        runtime = _runtime_with_robot(
            tool_rack,
            DeviceCapability.TOOL_RACK,
        )
        handler = ChangeToolActionHandler(runtime)
        context, _logs = _action_context()

        self.assertTrue(
            handler(
                {"Gun_Position": "2", "Operation": " 放 "},
                context,
            ).successful
        )
        self.assertEqual([(2, False)], tool_rack.calls)

    def test_invalid_change_tool_parameters_do_not_initialize_robot(self):
        created = 0

        def factory() -> _ToolRack:
            nonlocal created
            created += 1
            return _ToolRack()

        runtime = DeviceRuntime()
        runtime.register(
            DeviceRegistration(
                device_id=ROBOT_SYSTEM,
                capabilities=frozenset({DeviceCapability.TOOL_RACK}),
                factory=factory,
                close=lambda _device: None,
            )
        )
        handler = ChangeToolActionHandler(runtime)
        context, _logs = _action_context()

        self.assertFalse(
            handler(
                {"Gun_Position": "3", "Operation": "取"},
                context,
            ).successful
        )
        self.assertEqual(0, created)


class VisionActionHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = ApplicationSettings.defaults()
        self.runtime = create_device_runtime(
            self.settings,
            simulation=True,
        )
        self.context, self.logs = _action_context()

    def tearDown(self) -> None:
        self.runtime.shutdown_all()

    def test_capture_executor_receives_runtime_owned_devices(self):
        received: list[tuple[object, object, dict]] = []

        def executor(robot, camera, parameters, settings, log) -> bool:
            received.append((robot, camera, parameters))
            self.assertIs(self.settings.vision, settings)
            log("capture executor called")
            return True

        handler = VisionCaptureActionHandler(
            self.runtime,
            self.settings.vision,
            executor,
        )

        self.assertTrue(
            handler(
                {"workflow": "bottle"},
                self.context,
            ).successful
        )
        robot, camera, parameters = received[0]
        self.assertIs(
            robot,
            self.runtime.require(ROBOT_SYSTEM, RobotSystem),
        )
        self.assertIs(camera, self.runtime.get_if_ready(CAMERA))
        self.assertEqual({"workflow": "bottle"}, parameters)
        self.assertIn(("capture executor called", "info"), self.logs)

    def test_relocalization_receives_shared_execution_context(self):
        domain_context = ExecutionContext()
        received_contexts: list[ExecutionContext] = []

        def executor(
            _robot,
            _camera,
            _parameters,
            execution_context,
            settings,
            log,
        ) -> bool:
            received_contexts.append(execution_context)
            self.assertIs(self.settings.vision, settings)
            log("relocalization executor called")
            return True

        handler = VisionRelocalizationActionHandler(
            self.runtime,
            domain_context,
            self.settings.vision,
            executor,
        )

        self.assertTrue(
            handler(
                {"station_id": "A"},
                self.context,
            ).successful
        )
        self.assertEqual([domain_context], received_contexts)
        self.assertIn(
            ("relocalization executor called", "info"),
            self.logs,
        )

    def test_vision_executor_does_not_swallow_cancellation(self):
        control = ExecutionControl()
        context, _logs = _action_context(control)

        def executor(
            _robot,
            _camera,
            _parameters,
            _settings,
            _log,
        ) -> bool:
            control.cancel()
            return True

        handler = VisionCaptureActionHandler(
            self.runtime,
            self.settings.vision,
            executor,
        )

        with self.assertRaises(ActionCancelledError):
            handler({}, context)


class DomainHandlerIntegrationTests(unittest.TestCase):
    def test_change_tool_and_trajectory_use_unified_registry(self):
        settings = replace(
            ApplicationSettings.defaults(),
            execution=ExecutionSettings(
                execution_trajectory_poll_interval_seconds=0.001,
            ),
        )
        services = create_application_services(settings, simulation=True)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.txt"
            path.write_text("trajectory", encoding="utf-8")
            definitions = (
                ActionDefinition(
                    id="tool",
                    name="change tool",
                    type=ActionType.CHANGE_GUN,
                    parameters={
                        "Gun_Position": "1",
                        "Operation": "取",
                    },
                ),
                ActionDefinition(
                    id="trajectory",
                    name="run trajectory",
                    type=ActionType.TRAJECTORY,
                    parameters={
                        "robot": "robot1",
                        "file_path": str(path),
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
        robot = services.device_runtime.require(
            ROBOT_SYSTEM,
            RobotSystem,
        )
        self.assertEqual(1, robot.tool_slot)
        self.assertFalse(services.devices.shutdown_all())


if __name__ == "__main__":
    unittest.main()
