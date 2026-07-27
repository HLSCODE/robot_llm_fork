from __future__ import annotations

from threading import Event
import unittest

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.device_runtime import (
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
    ResourceArbiter,
    ResourceBusyError,
)
from src.execution import (
    EngineCallbacks,
    EngineResult,
    ExecutionAlreadyRunningError,
    ExecutionControl,
    ExecutionEventType,
    ExecutionManager,
    ExecutionState,
)


class _CloseTracker:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
        callbacks.on_step_started(0, sequence[0])
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


class ExecutionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _BlockingEngine()
        self.resources = ResourceArbiter()
        self.events = []
        self.manager = ExecutionManager(
            engine=self.engine,
            resource_arbiter=self.resources,
            execution_resources=lambda: ("robot",),
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
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 0.01},
            )
        )

        with self.assertRaises(ResourceBusyError):
            services.execution.start([item], origin="test")

        services.teleoperation.stop()
        final = services.execution.start([item], origin="test").wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)


if __name__ == "__main__":
    unittest.main()
