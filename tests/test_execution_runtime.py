from __future__ import annotations

from threading import Event
import unittest

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.device_runtime import (
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
    DeviceStopStatus,
    ResourceArbiter,
    ResourceBusyError,
    RobotSystem,
    StopMode,
)
from src.device_runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from src.execution import (
    EngineCallbacks,
    EngineResult,
    ExecutionAlreadyRunningError,
    ExecutionControl,
    ExecutionEventType,
    ExecutionManager,
    ExecutionState,
)
from src.execution.action_control import resolve_wait_control_policy


class _CloseTracker:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StoppableMotion(_CloseTracker):
    def __init__(
        self,
        modes: frozenset[StopMode],
        *,
        fail: bool = False,
    ) -> None:
        super().__init__()
        self.supported_stop_modes = modes
        self.fail = fail
        self.stops: list[StopMode] = []

    def stop(self, mode: StopMode) -> None:
        self.stops.append(mode)
        if self.fail:
            raise RuntimeError("stop rejected")


class _BlockingEngine:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def run(
        self,
        sequence,
        control: ExecutionControl,
        callbacks: EngineCallbacks,
    ) -> EngineResult:
        self.started.set()
        callbacks.on_step_started(
            0,
            sequence[0],
            resolve_wait_control_policy({}),
        )
        while not self.release.is_set():
            if not control.sleep(0.01):
                return EngineResult(success=False, cancelled=True)
        callbacks.on_step_completed(0, sequence[0])
        return EngineResult(success=True)


class DeviceRuntimeTests(unittest.TestCase):
    def test_runtime_owns_one_instance_and_can_reinitialize_after_shutdown(self):
        runtime = DeviceRuntime()
        created: list[_CloseTracker] = []

        def factory() -> _CloseTracker:
            instance = _CloseTracker()
            created.append(instance)
            return instance

        runtime.register(
            DeviceRegistration(
                device_id="test-device",
                capabilities=frozenset({DeviceCapability.DIGITAL_OUTPUT}),
                factory=factory,
                close=lambda device: device.close(),
            )
        )

        first = runtime.initialize("test-device")
        self.assertIs(first, runtime.initialize("test-device"))
        self.assertEqual(1, len(created))

        runtime.shutdown("test-device")
        self.assertTrue(first.closed)
        second = runtime.initialize("test-device")
        self.assertIsNot(first, second)
        self.assertEqual(2, len(created))

    def test_resource_arbiter_rejects_concurrent_owner(self):
        arbiter = ResourceArbiter()
        lease = arbiter.acquire("owner-a", ("robot",))
        with self.assertRaises(ResourceBusyError):
            arbiter.acquire("owner-b", ("robot",))

        lease.release()
        next_lease = arbiter.acquire("owner-b", ("robot",))
        self.assertEqual("owner-b", arbiter.owner_of("robot"))
        next_lease.release()

    def test_stop_all_reports_each_motion_device_and_continues_on_failure(self):
        runtime = DeviceRuntime()
        quick = _StoppableMotion(frozenset({StopMode.QUICK}))
        failed = _StoppableMotion(
            frozenset({StopMode.QUICK}),
            fail=True,
        )
        registrations = (
            ("not-ready", {DeviceCapability.MOTION}, _CloseTracker()),
            (
                "unsupported",
                {DeviceCapability.MOTION},
                _CloseTracker(),
            ),
            (
                "failed",
                {
                    DeviceCapability.MOTION,
                    DeviceCapability.QUICK_STOP,
                },
                failed,
            ),
            (
                "quick",
                {
                    DeviceCapability.MOTION,
                    DeviceCapability.QUICK_STOP,
                },
                quick,
            ),
        )
        for device_id, capabilities, instance in registrations:
            runtime.register(
                DeviceRegistration(
                    device_id=device_id,
                    capabilities=frozenset(capabilities),
                    factory=lambda instance=instance: instance,
                    close=lambda device: device.close(),
                )
            )
        for device_id in ("unsupported", "failed", "quick"):
            runtime.initialize(device_id)

        with self.assertLogs(
            "src.device_runtime.runtime",
            level="WARNING",
        ):
            results = {
                result.device_id: result
                for result in runtime.stop_all(StopMode.QUICK)
            }

        self.assertEqual(DeviceStopStatus.STOPPED, results["quick"].status)
        self.assertEqual(DeviceStopStatus.FAILED, results["failed"].status)
        self.assertEqual(
            DeviceStopStatus.UNSUPPORTED,
            results["unsupported"].status,
        )
        self.assertEqual(
            DeviceStopStatus.NOT_READY,
            results["not-ready"].status,
        )
        self.assertEqual([StopMode.QUICK], quick.stops)
        self.assertEqual([StopMode.QUICK], failed.stops)

    def test_controlled_stop_is_not_a_device_runtime_operation(self):
        runtime = DeviceRuntime()
        with self.assertRaises(ValueError):
            runtime.stop_all(StopMode.CONTROLLED)

    def test_declared_stop_modes_come_from_registration_capabilities(self):
        runtime = DeviceRuntime()
        runtime.register(
            DeviceRegistration(
                device_id="quick-only",
                capabilities=frozenset(
                    {
                        DeviceCapability.MOTION,
                        DeviceCapability.QUICK_STOP,
                    }
                ),
                factory=_CloseTracker,
                close=lambda device: device.close(),
            )
        )

        self.assertEqual(
            frozenset({StopMode.QUICK}),
            runtime.declared_stop_modes("quick-only"),
        )


class ExecutionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _BlockingEngine()
        self.resources = ResourceArbiter()
        self.events = []
        self.manager = ExecutionManager(
            engine=self.engine,
            resource_arbiter=self.resources,
            execution_resources=lambda _sequence: ("robot",),
        )

    def test_single_run_pause_resume_and_completion(self):
        handle = self.manager.submit(
            ["step"],
            origin="test",
            listener=self.events.append,
        )
        self.assertTrue(self.engine.started.wait(1))

        with self.assertRaises(ExecutionAlreadyRunningError):
            self.manager.submit(["other"], origin="test")

        handle.pause()
        self.assertEqual(ExecutionState.PAUSED, handle.snapshot().state)
        handle.resume()
        self.assertEqual(ExecutionState.RUNNING, handle.snapshot().state)

        self.engine.release.set()
        final = handle.wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)
        self.assertIsNone(self.resources.owner_of("robot"))
        event_types = [event.event_type for event in self.events]
        self.assertIn(ExecutionEventType.STEP_STARTED, event_types)
        self.assertIn(ExecutionEventType.SUCCEEDED, event_types)

    def test_cancel_releases_resources(self):
        handle = self.manager.submit(["step"], origin="test")
        self.assertTrue(self.engine.started.wait(1))
        handle.cancel()
        final = handle.wait(1)
        self.assertEqual(ExecutionState.CANCELLED, final.state)
        self.assertIsNone(self.resources.owner_of("robot"))

    def test_new_run_waits_until_terminal_event_delivery_finishes(self):
        terminal_received = Event()
        release_listener = Event()

        def listener(event):
            if event.event_type is ExecutionEventType.SUCCEEDED:
                terminal_received.set()
                release_listener.wait(1)

        handle = self.manager.submit(
            ["step"],
            origin="test",
            listener=listener,
        )
        self.assertTrue(self.engine.started.wait(1))
        self.engine.release.set()
        self.assertTrue(terminal_received.wait(1))
        self.assertEqual(ExecutionState.SUCCEEDED, handle.snapshot().state)

        with self.assertRaises(ExecutionAlreadyRunningError):
            self.manager.submit(["other"], origin="test")

        release_listener.set()
        self.assertEqual(ExecutionState.SUCCEEDED, handle.wait(1).state)


class ApplicationServiceTests(unittest.TestCase):
    def test_simulated_wait_action_uses_unified_runtime(self):
        services = create_application_services(object(), simulation=True)
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 0.01},
            )
        )

        handle = services.execution.start([item], origin="test")
        final = handle.wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)
        self.assertFalse(services.devices.shutdown_all())

    def test_teleoperation_session_blocks_sequence_execution(self):
        services = create_application_services(object(), simulation=True)
        services.teleoperation.start()
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="move",
                name="move",
                type=ActionType.MOVE,
                parameters={
                    "目标": "机械臂",
                    "臂": "左",
                    "模式": "move_j",
                    "点位": [0, 0, 0, 0, 0, 0],
                },
            )
        )

        with self.assertRaises(ResourceBusyError):
            services.execution.start([item], origin="test")

        services.teleoperation.stop()
        final = services.execution.start([item], origin="test").wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)

    def test_quick_stop_cancels_execution_and_stops_ready_robot(self):
        services = create_application_services(object(), simulation=True)
        robot = services.device_runtime.require(ROBOT_SYSTEM, RobotSystem)
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 5},
            )
        )
        services.execution.start([item], origin="test")

        report = services.safety.stop(
            StopMode.QUICK,
            wait_timeout_seconds=1,
        )

        self.assertTrue(report.complete)
        self.assertEqual(StopMode.QUICK, robot.last_stop_mode)
        self.assertEqual(ExecutionState.CANCELLED, report.execution_after.state)
        self.assertIsNone(services.resources.owner_of(ROBOT_SYSTEM))

    def test_quick_stop_exposes_ready_unsupported_motion_device(self):
        services = create_application_services(object(), simulation=True)
        services.device_runtime.initialize(BODY_AXIS)

        report = services.safety.stop(StopMode.QUICK)

        body_result = next(
            result
            for result in report.devices
            if result.device_id == BODY_AXIS
        )
        self.assertEqual(DeviceStopStatus.UNSUPPORTED, body_result.status)
        self.assertFalse(report.complete)

    def test_quick_stop_releases_teleoperation_session(self):
        services = create_application_services(object(), simulation=True)
        services.teleoperation.start()
        robot = services.device_runtime.require(ROBOT_SYSTEM, RobotSystem)

        report = services.safety.stop(StopMode.QUICK)

        self.assertTrue(report.complete)
        self.assertFalse(services.teleoperation.active)
        self.assertIsNone(services.resources.owner_of(ROBOT_SYSTEM))
        self.assertEqual(StopMode.QUICK, robot.last_stop_mode)


if __name__ == "__main__":
    unittest.main()
