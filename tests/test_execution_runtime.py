from __future__ import annotations

from threading import Barrier, Event, Lock, Thread
import logging
import unittest

from src.application import create_application_services
from src.domain.models import (
    ActionDefinition,
    ActionType,
    ParallelBlock,
    ParallelBranch,
    SequenceItem,
)
from src.domain.execution_plan import ExecutionPlan, iter_execution_steps
from src.observability.logging_config import LoggingContextFilter
from src.configuration.settings import ApplicationSettings
from src.devices import (
    DeviceCapability,
    DeviceErrorCategory,
    DeviceOperationError,
    DeviceRegistration,
    DeviceRuntime,
    DeviceSafeStateStatus,
    DeviceStopStatus,
    ResourceArbiter,
    ResourceBusyError,
    StopMode,
)
from src.devices.runtime.ids import BODY_AXIS, ROBOT_SYSTEM
from src.execution import (
    EngineCallbacks,
    EngineResult,
    ExecutionAlreadyRunningError,
    ExecutionControl,
    ExecutionEventType,
    ExecutionManager,
    ExecutionState,
    ExecutionStateError,
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
        plan: ExecutionPlan,
        control: ExecutionControl,
        callbacks: EngineCallbacks,
    ) -> EngineResult:
        self.started.set()
        identity, item = next(iter_execution_steps(plan))
        callbacks.on_step_started(
            identity,
            item,
            resolve_wait_control_policy({}),
        )
        while not self.release.is_set():
            if not control.sleep(0.01):
                return EngineResult(success=False, cancelled=True)
        callbacks.on_step_completed(identity, item)
        return EngineResult(success=True)


class _ManualWorker:
    """Deterministic worker that starts only when the test invokes run()."""

    def __init__(self, target) -> None:
        self._target = target
        self._started = False
        self._alive = False

    def start(self) -> None:
        self._started = True
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def run(self) -> None:
        if not self._started:
            raise RuntimeError("manual worker has not been started")
        try:
            self._target()
        finally:
            self._alive = False


class _FailingWorker:
    def start(self) -> None:
        raise RuntimeError("worker start rejected")

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        del timeout


def _test_plan(action_id: str) -> ExecutionPlan:
    return ExecutionPlan.from_entries((SequenceItem.from_definition(
        ActionDefinition(
            id=action_id,
            name=action_id,
            type=ActionType.WAIT,
            parameters={"wait_seconds": 0.01},
        )
    ),))


def _parallel_wait(action_id: str) -> SequenceItem:
    return SequenceItem.from_definition(ActionDefinition(
        id=action_id,
        name=action_id,
        type=ActionType.WAIT,
        parameters={"wait_seconds": 2.0},
    ))


class DeviceRuntimeTests(unittest.TestCase):
    def test_require_does_not_retry_a_known_failed_device(self):
        runtime = DeviceRuntime()
        attempts = 0

        def failing_factory() -> _CloseTracker:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("robot is offline")

        runtime.register(
            DeviceRegistration(
                device_id="offline-robot",
                capabilities=frozenset({DeviceCapability.MOTION}),
                factory=failing_factory,
                close=lambda device: device.close(),
            )
        )

        with self.assertRaises(DeviceOperationError):
            runtime.initialize("offline-robot")
        self.assertEqual(1, attempts)

        with self.assertRaises(DeviceOperationError) as raised:
            runtime.require("offline-robot")

        self.assertEqual(1, attempts)
        self.assertEqual("device.require", raised.exception.operation)
        self.assertEqual(
            DeviceErrorCategory.UNAVAILABLE,
            raised.exception.category,
        )

        # A user-initiated reconnect remains possible through the explicit API.
        with self.assertRaises(DeviceOperationError):
            runtime.initialize("offline-robot")
        self.assertEqual(2, attempts)

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
            "src.devices.runtime.runtime",
            level="WARNING",
        ):
            results = {
                result.device_id: result
                for result in runtime.stop_all(StopMode.QUICK)
            }

        self.assertEqual(DeviceStopStatus.STOPPED, results["quick"].status)
        self.assertEqual(DeviceStopStatus.FAILED, results["failed"].status)
        self.assertEqual("internal", results["failed"].error_category)
        self.assertEqual(
            "设备操作失败（设备=failed，操作=device.stop.quick）",
            results["failed"].error,
        )
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

    def test_safe_state_policies_are_applied_and_reported_independently(self):
        runtime = DeviceRuntime()
        safe_calls: list[str] = []
        for device_id in ("relay", "tool"):
            runtime.register(
                DeviceRegistration(
                    device_id=device_id,
                    capabilities=frozenset({DeviceCapability.SAFE_STATE}),
                    factory=_CloseTracker,
                    close=lambda device: device.close(),
                    enter_safe_state=(
                        lambda _device, value=device_id: safe_calls.append(value)
                    ),
                )
            )
        runtime.initialize("relay")

        results = {
            result.device_id: result
            for result in runtime.enter_safe_states()
        }

        self.assertEqual(["relay"], safe_calls)
        self.assertEqual(
            DeviceSafeStateStatus.APPLIED,
            results["relay"].status,
        )
        self.assertEqual(
            DeviceSafeStateStatus.NOT_READY,
            results["tool"].status,
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
            _test_plan("step"),
            origin="test",
            listener=self.events.append,
        )
        self.assertTrue(self.engine.started.wait(1))

        with self.assertRaises(ExecutionAlreadyRunningError):
            self.manager.submit(_test_plan("other"), origin="test")

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
        handle = self.manager.submit(_test_plan("step"), origin="test")
        self.assertTrue(self.engine.started.wait(1))
        handle.cancel()
        final = handle.wait(1)
        self.assertEqual(ExecutionState.CANCELLED, final.state)
        self.assertIsNone(self.resources.owner_of("robot"))

    def test_cancel_before_worker_runs_never_regresses_to_running(self):
        workers: list[_ManualWorker] = []

        def create_worker(target, _name: str) -> _ManualWorker:
            worker = _ManualWorker(target)
            workers.append(worker)
            return worker

        manager = ExecutionManager(
            engine=self.engine,
            resource_arbiter=self.resources,
            execution_resources=lambda _sequence: ("robot",),
            worker_factory=create_worker,
        )
        events = []
        handle = manager.submit(
            _test_plan("step"),
            origin="test",
            listener=events.append,
        )

        handle.cancel()
        self.assertEqual(ExecutionState.CANCELLING, handle.snapshot().state)
        workers[0].run()

        self.assertEqual(ExecutionState.CANCELLED, handle.snapshot().state)
        self.assertFalse(self.engine.started.is_set())
        self.assertIsNone(self.resources.owner_of("robot"))
        self.assertEqual(
            [
                ExecutionEventType.ACCEPTED,
                ExecutionEventType.CANCELLING,
                ExecutionEventType.CANCELLED,
            ],
            [event.event_type for event in events],
        )

    def test_worker_start_failure_is_terminal_and_releases_resources(self):
        events = []
        manager = ExecutionManager(
            engine=self.engine,
            resource_arbiter=self.resources,
            execution_resources=lambda _sequence: ("robot",),
            worker_factory=lambda _target, _name: _FailingWorker(),
        )

        with self.assertRaisesRegex(RuntimeError, "worker start rejected"):
            manager.submit(
                _test_plan("step"),
                origin="test",
                listener=events.append,
            )

        snapshot = manager.snapshot()
        self.assertEqual(ExecutionState.FAILED, snapshot.state)
        self.assertEqual("internal_error", snapshot.error_code)
        self.assertEqual("execution.worker.start", snapshot.error_operation)
        self.assertIsNone(self.resources.owner_of("robot"))
        self.assertEqual(
            [ExecutionEventType.ACCEPTED, ExecutionEventType.FAILED],
            [event.event_type for event in events],
        )

    def test_concurrent_submissions_accept_exactly_one_run(self):
        contender_count = 8
        barrier = Barrier(contender_count)
        result_lock = Lock()
        handles = []
        rejections: list[ExecutionAlreadyRunningError] = []

        def submit() -> None:
            barrier.wait()
            try:
                handle = self.manager.submit(_test_plan("step"), origin="race")
            except ExecutionAlreadyRunningError as exc:
                with result_lock:
                    rejections.append(exc)
            else:
                with result_lock:
                    handles.append(handle)

        contenders = [Thread(target=submit) for _ in range(contender_count)]
        for contender in contenders:
            contender.start()
        for contender in contenders:
            contender.join(timeout=1)

        self.assertEqual(1, len(handles))
        self.assertEqual(contender_count - 1, len(rejections))
        handles[0].cancel()
        self.assertEqual(ExecutionState.CANCELLED, handles[0].wait(1).state)
        self.assertIsNone(self.resources.owner_of("robot"))

    def test_cancel_completion_race_emits_one_terminal_event_last(self):
        events = []
        event_lock = Lock()

        def listener(event) -> None:
            with event_lock:
                events.append(event)

        handle = self.manager.submit(
            _test_plan("step"),
            origin="race",
            listener=listener,
        )
        self.assertTrue(self.engine.started.wait(1))
        barrier = Barrier(2)
        cancel_errors = []

        def cancel() -> None:
            barrier.wait()
            try:
                handle.cancel()
            except ExecutionStateError as exc:
                cancel_errors.append(exc)

        def complete() -> None:
            barrier.wait()
            self.engine.release.set()

        cancel_thread = Thread(target=cancel)
        complete_thread = Thread(target=complete)
        cancel_thread.start()
        complete_thread.start()
        cancel_thread.join(timeout=1)
        complete_thread.join(timeout=1)
        final = handle.wait(1)

        self.assertIn(
            final.state,
            {ExecutionState.SUCCEEDED, ExecutionState.CANCELLED},
        )
        self.assertLessEqual(len(cancel_errors), 1)
        terminal_events = [
            event
            for event in events
            if event.event_type
            in {
                ExecutionEventType.SUCCEEDED,
                ExecutionEventType.FAILED,
                ExecutionEventType.CANCELLED,
            }
        ]
        self.assertEqual(1, len(terminal_events))
        self.assertIs(terminal_events[0], events[-1])
        self.assertIsNone(self.resources.owner_of("robot"))

    def test_worker_logs_are_correlated_with_run_id(self):
        records: list[logging.LogRecord] = []

        class RecordHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = RecordHandler()
        handler.addFilter(LoggingContextFilter())
        manager_logger = logging.getLogger("src.execution.manager")
        manager_logger.addHandler(handler)
        manager_logger.setLevel(logging.INFO)
        try:
            handle = self.manager.submit(_test_plan("step"), origin="test")
            self.assertTrue(self.engine.started.wait(1))
            self.engine.release.set()
            handle.wait(1)
        finally:
            manager_logger.removeHandler(handler)

        execution_records = [
            record
            for record in records
            if record.getMessage().startswith("Execution ")
        ]
        self.assertEqual(2, len(execution_records))
        self.assertTrue(all(record.run_id == handle.run_id for record in execution_records))
        self.assertTrue(
            all(record.operation == "execution.run" for record in execution_records)
        )

    def test_new_run_waits_until_terminal_event_delivery_finishes(self):
        terminal_received = Event()
        release_listener = Event()

        def listener(event):
            if event.event_type is ExecutionEventType.SUCCEEDED:
                terminal_received.set()
                release_listener.wait(1)

        handle = self.manager.submit(
            _test_plan("step"),
            origin="test",
            listener=listener,
        )
        self.assertTrue(self.engine.started.wait(1))
        self.engine.release.set()
        self.assertTrue(terminal_received.wait(1))
        self.assertEqual(ExecutionState.SUCCEEDED, handle.snapshot().state)

        with self.assertRaises(ExecutionAlreadyRunningError):
            self.manager.submit(_test_plan("other"), origin="test")

        release_listener.set()
        self.assertEqual(ExecutionState.SUCCEEDED, handle.wait(1).state)


class ApplicationServiceTests(unittest.TestCase):
    def test_parallel_run_pause_resume_and_cancel_reaches_one_terminal_state(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        branch_started = Event()
        events = []

        def listener(event) -> None:
            events.append(event)
            if sum(
                item.event_type is ExecutionEventType.PARALLEL_BRANCH_STARTED
                for item in events
            ) == 2:
                branch_started.set()

        parallel = ParallelBlock(
            uuid="parallel-control",
            branches=[
                ParallelBranch("left", [_parallel_wait("left")]),
                ParallelBranch("right", [_parallel_wait("right")]),
            ],
        )
        handle = services.execution.start_entries(
            [parallel],
            origin="test",
            listener=listener,
        )
        self.assertTrue(branch_started.wait(1))

        handle.pause()
        self.assertEqual(ExecutionState.PAUSED, handle.snapshot().state)
        handle.resume()
        self.assertEqual(ExecutionState.RUNNING, handle.snapshot().state)
        handle.cancel()

        self.assertEqual(ExecutionState.CANCELLED, handle.wait(1).state)
        terminal = [
            event for event in events
            if event.event_type in {
                ExecutionEventType.SUCCEEDED,
                ExecutionEventType.FAILED,
                ExecutionEventType.CANCELLED,
            }
        ]
        self.assertEqual(
            [ExecutionEventType.CANCELLED],
            [event.event_type for event in terminal],
        )
        self.assertEqual(2, sum(
            event.event_type is ExecutionEventType.PARALLEL_BRANCH_CANCELLED
            for event in events
        ))

    def test_simulated_wait_action_uses_unified_runtime(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 0.01},
            )
        )

        handle = services.execution.start_entries([item], origin="test")
        final = handle.wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)
        self.assertFalse(services.devices.shutdown_all())

    def test_teleoperation_session_blocks_sequence_execution(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.teleoperation.start("test", ("left",))
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
            services.execution.start_entries([item], origin="test")

        services.teleoperation.stop("test")
        final = services.execution.start_entries([item], origin="test").wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, final.state)

    def test_quick_stop_cancels_execution_and_stops_ready_robot(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.devices.initialize(ROBOT_SYSTEM)
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 5},
            )
        )
        services.execution.start_entries([item], origin="test")

        report = services.safety.stop(
            StopMode.QUICK,
            wait_timeout_seconds=1,
        )

        self.assertTrue(report.complete)
        robot_result = next(
            result for result in report.devices
            if result.device_id == ROBOT_SYSTEM
        )
        self.assertEqual(StopMode.QUICK, robot_result.mode)
        self.assertEqual(DeviceStopStatus.STOPPED, robot_result.status)
        self.assertEqual(ExecutionState.CANCELLED, report.execution_after.state)
        self.assertIsNone(services.resources.owner_of(ROBOT_SYSTEM))

    def test_quick_stop_exposes_ready_unsupported_motion_device(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.devices.initialize(BODY_AXIS)

        report = services.safety.stop(StopMode.QUICK)

        body_result = next(
            result
            for result in report.devices
            if result.device_id == BODY_AXIS
        )
        self.assertEqual(DeviceStopStatus.UNSUPPORTED, body_result.status)
        self.assertFalse(report.complete)

    def test_quick_stop_releases_teleoperation_session(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.teleoperation.start("test", ("left",))

        report = services.safety.stop(StopMode.QUICK)

        self.assertTrue(report.complete)
        self.assertFalse(services.teleoperation.active)
        self.assertIsNone(services.resources.owner_of(ROBOT_SYSTEM))
        robot_result = next(
            result for result in report.devices
            if result.device_id == ROBOT_SYSTEM
        )
        self.assertEqual(DeviceStopStatus.STOPPED, robot_result.status)

    def test_controlled_stop_applies_initialized_discrete_device_safe_states(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        services.devices.initialize_many(
            ("relay-bank", "tool-changer", "pipette")
        )
        services.manual_control.set_relay(1, True)

        report = services.safety.stop(StopMode.CONTROLLED)

        self.assertTrue(report.complete)
        self.assertEqual(
            {"relay-bank", "tool-changer", "pipette"},
            {result.device_id for result in report.safe_devices},
        )
        self.assertTrue(all(result.successful for result in report.safe_devices))


if __name__ == "__main__":
    unittest.main()
