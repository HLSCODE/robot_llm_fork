from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from contextlib import contextmanager
from types import ModuleType
from unittest.mock import patch

from src.application import (
    CameraAccessService,
    DataCollectionState,
    create_application_services,
)
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.configuration.settings import ApplicationSettings
from src.devices import (
    DeviceCapability,
    DeviceContractError,
    DeviceErrorCategory,
    DeviceOperationError,
    DeviceRegistration,
    DeviceRuntime,
    DeviceState,
    ResourceArbiter,
    ResourceBusyError,
)
from src.devices.runtime.ids import CAMERA, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.robot_server.ws_server import RobotWebSocketServer
from src.voice_interaction import CameraCaptureError, CamerasModuleProvider


class _ProbeCamera:
    def __init__(self) -> None:
        self._running = True
        self.activated_names: tuple[str, ...] = ()

    @property
    def camera_count(self) -> int:
        return 1 if self._running else 0

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> dict[str, object]:
        self._running = True
        return {"left": True}

    def activate(self, camera_names=()) -> dict[str, object]:
        self.activated_names = tuple(camera_names)
        self._running = True
        return {"started": 1, "failed": 0}

    def stop(self) -> None:
        self._running = False

    def get_cameras_info(self) -> list[dict[str, object]]:
        return [{"serial": "001", "name": "left", "online": self._running}]

    def get_latest_jpegs(self) -> list[tuple[str, str, bytes]]:
        if not self._running:
            return []
        return [("001", "left", b"jpeg")]


class CameraAccessServiceTests(unittest.TestCase):
    def test_session_activates_selected_camera_and_closes_after_idle_timeout(self):
        runtime = DeviceRuntime()
        resources = ResourceArbiter()
        camera = _ProbeCamera()
        runtime.register(
            DeviceRegistration(
                device_id=CAMERA,
                capabilities=frozenset({DeviceCapability.CAMERA}),
                factory=lambda: camera,
                close=lambda instance: instance.stop(),
            )
        )
        service = CameraAccessService(
            runtime,
            resources,
            idle_timeout_seconds=0.01,
        )

        session = service.open("selected", camera_names=("left",))
        self.assertEqual(("left",), camera.activated_names)
        session.close()

        deadline = time.monotonic() + 1.0
        while runtime.snapshot(CAMERA).state is not DeviceState.STOPPED:
            if time.monotonic() >= deadline:
                self.fail("camera runtime did not return to STOPPED")
            time.sleep(0.01)
        self.assertFalse(camera.is_running)

    def test_provider_probe_does_not_initialize_streaming_runtime(self):
        runtime = DeviceRuntime()
        resources = ResourceArbiter()
        runtime_factory_called = False
        probe_arguments: list[tuple[float, int]] = []

        def create_camera() -> _ProbeCamera:
            nonlocal runtime_factory_called
            runtime_factory_called = True
            return _ProbeCamera()

        def probe(timeout: float, attempts: int):
            probe_arguments.append((timeout, attempts))
            return (
                {
                    "serial": "001",
                    "name": "left",
                    "online": True,
                    "frame_received": True,
                },
            )

        runtime.register(
            DeviceRegistration(
                device_id=CAMERA,
                capabilities=frozenset({DeviceCapability.CAMERA}),
                factory=create_camera,
                close=lambda camera: camera.stop(),
            )
        )
        service = CameraAccessService(
            runtime,
            resources,
            configured_cameras=(
                {
                    "serial": "001",
                    "name": "left",
                    "label": "左臂相机",
                    "required": True,
                },
            ),
            probe=probe,
            probe_timeout_seconds=2.5,
            probe_max_attempts=2,
        )

        status = service.probe_all()

        self.assertFalse(runtime_factory_called)
        self.assertEqual([(2.5, 2)], probe_arguments)
        self.assertEqual(DeviceState.STOPPED, runtime.snapshot(CAMERA).state)
        self.assertEqual("左臂相机", status.cameras[0]["label"])
        self.assertTrue(status.cameras[0]["required"])

    def test_probe_all_stops_pipeline_and_caches_health_snapshot(self):
        runtime = DeviceRuntime()
        resources = ResourceArbiter()
        instances: list[_ProbeCamera] = []

        def create_camera() -> _ProbeCamera:
            camera = _ProbeCamera()
            instances.append(camera)
            return camera

        runtime.register(
            DeviceRegistration(
                device_id=CAMERA,
                capabilities=frozenset({DeviceCapability.CAMERA}),
                factory=create_camera,
                close=lambda camera: camera.stop(),
            )
        )
        service = CameraAccessService(
            runtime,
            resources,
            configured_cameras=({"serial": "001", "name": "left"},),
        )

        status = service.probe_all(frame_timeout_seconds=0.1)

        self.assertTrue(status.available)
        self.assertEqual(1, status.camera_count)
        self.assertTrue(status.cameras[0]["frame_received"])
        self.assertFalse(instances[0].is_running)
        self.assertEqual(DeviceState.STOPPED, runtime.snapshot(CAMERA).state)
        self.assertIsNone(resources.owner_of(CAMERA))
        self.assertEqual(status, service.status())

        session = service.open("after-probe")
        self.assertEqual(2, len(instances))
        self.assertTrue(session.camera.is_running)
        session.close()
        runtime.shutdown(CAMERA)

    def test_probe_failure_returns_runtime_to_stopped_and_releases_lease(self):
        runtime = DeviceRuntime()
        resources = ResourceArbiter()

        def fail_to_create_camera() -> _ProbeCamera:
            raise RuntimeError("offline")

        runtime.register(
            DeviceRegistration(
                device_id=CAMERA,
                capabilities=frozenset({DeviceCapability.CAMERA}),
                factory=fail_to_create_camera,
                close=lambda _camera: None,
            )
        )
        service = CameraAccessService(
            runtime,
            resources,
            configured_cameras=({"serial": "001", "name": "left"},),
        )

        with self.assertRaises(DeviceOperationError):
            service.probe_all(frame_timeout_seconds=0.1)

        self.assertEqual(DeviceState.STOPPED, runtime.snapshot(CAMERA).state)
        self.assertIsNone(resources.owner_of(CAMERA))
        self.assertFalse(service.status().available)
        self.assertTrue(service.status().cameras[0]["error"])

    def test_status_returns_presentation_safe_snapshot(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )

        self.assertEqual(
            {"available": False, "camera_count": 0, "cameras": []},
            services.camera_access.status().to_dict(),
        )

        session = services.camera_access.open("status-test")
        status = services.camera_access.status()

        self.assertTrue(status.available)
        self.assertGreater(status.camera_count, 0)
        self.assertEqual(status.camera_count, len(status.cameras))
        session.close()
        self.assertFalse(services.devices.shutdown_all())

    def test_camera_session_only_blocks_sequences_that_need_camera(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        session = services.camera_access.open("test")
        wait_item = SequenceItem.from_definition(
            ActionDefinition(
                id="wait",
                name="wait",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 0.01},
            )
        )
        vision_item = SequenceItem.from_definition(
            ActionDefinition(
                id="vision",
                name="vision",
                type=ActionType.VISION_CAPTURE,
                parameters={},
            )
        )

        self.assertTrue(session.active)
        self.assertTrue(services.resources.owner_of(CAMERA).startswith("camera:test:"))
        wait_result = services.execution.start_entries(
            [wait_item],
            origin="test",
        ).wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, wait_result.state)
        with self.assertRaises(ResourceBusyError):
            services.execution.start_entries([vision_item], origin="test")

        session.close()
        session.close()
        self.assertFalse(session.active)
        self.assertIsNone(services.resources.owner_of(CAMERA))
        self.assertFalse(services.devices.shutdown_all())

    def test_failed_camera_contract_releases_lease(self):
        runtime = DeviceRuntime()
        resources = ResourceArbiter()
        runtime.register(
            DeviceRegistration(
                device_id=CAMERA,
                capabilities=frozenset({DeviceCapability.CAMERA}),
                factory=object,
                close=lambda _device: None,
            )
        )
        service = CameraAccessService(runtime, resources)

        with self.assertRaises(DeviceContractError):
            service.open("invalid-contract")

        self.assertIsNone(resources.owner_of(CAMERA))

    def test_device_lifecycle_refuses_an_active_camera_session(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        session = services.camera_access.open("lifecycle-test")

        with self.assertRaises(ResourceBusyError):
            services.devices.initialize(CAMERA)
        with self.assertRaises(ResourceBusyError):
            services.devices.shutdown_all()

        session.close()
        self.assertFalse(services.devices.shutdown_all())

    def test_failed_device_initialization_releases_lifecycle_lease(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )

        with self.assertRaises(DeviceOperationError) as raised:
            services.devices.initialize("missing-device")

        self.assertEqual(
            DeviceErrorCategory.UNAVAILABLE,
            raised.exception.category,
        )
        self.assertIsNone(services.resources.owner_of("missing-device"))
        self.assertFalse(services.devices.shutdown_all())


class CameraProviderSessionTests(unittest.TestCase):
    def test_capture_releases_session_after_success(self):
        closed = False

        class Camera:
            def get_latest_jpegs(self):
                return [("serial", "camera", b"jpeg")]

        @contextmanager
        def session():
            nonlocal closed
            try:
                yield Camera()
            finally:
                closed = True

        provider = CamerasModuleProvider(
            session_factory=session,
            wait_timeout_s=0,
        )

        parts = provider.capture_llm_parts()

        self.assertEqual(1, len(parts))
        self.assertTrue(closed)

    def test_capture_releases_session_after_camera_error(self):
        closed = False

        class Camera:
            def get_latest_jpegs(self):
                return []

            def get_cameras_info(self):
                return []

        @contextmanager
        def session():
            nonlocal closed
            try:
                yield Camera()
            finally:
                closed = True

        provider = CamerasModuleProvider(
            session_factory=session,
            wait_timeout_s=0,
        )

        with self.assertRaises(CameraCaptureError):
            provider.capture_llm_parts()

        self.assertTrue(closed)


class CameraWebSocketSessionTests(unittest.TestCase):
    def test_preview_holds_camera_until_last_subscriber_leaves(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        server = RobotWebSocketServer(services)
        websocket = _RecordingWebSocket()
        server._register_client(websocket, websocket.remote_address)

        async def scenario() -> None:
            await server._device_handler._handle_subscribe_camera_frames(websocket, {})
            self.assertIsNotNone(services.resources.owner_of(CAMERA))

            await server._device_handler._handle_unsubscribe_camera_frames(websocket, {})
            self.assertIsNone(services.resources.owner_of(CAMERA))
            await server._cancel_background_tasks()

        asyncio.run(scenario())
        self.assertEqual(
            ["camera_subscribed", "camera_unsubscribed"],
            [json.loads(message)["event"] for message in websocket.messages],
        )
        self.assertFalse(services.devices.shutdown_all())

    def test_preview_failure_releases_camera_session(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        server = RobotWebSocketServer(services)
        websocket = _RecordingWebSocket()

        async def scenario() -> None:
            await server._device_handler._handle_subscribe_camera_frames(websocket, {})
            session = server._camera_preview_session
            self.assertIsNotNone(session)

            def fail_capture():
                raise RuntimeError("capture failed")

            session.camera.get_latest_jpegs = fail_capture
            with patch("src.robot_server.ws_server.logger.error"):
                await asyncio.wait_for(server._camera_push_task, timeout=1)

            self.assertFalse(server._camera_frame_subs)
            self.assertIsNone(services.resources.owner_of(CAMERA))

        asyncio.run(scenario())
        self.assertFalse(services.devices.shutdown_all())

    def test_data_collection_holds_camera_and_teleoperation_resources(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        server = RobotWebSocketServer(services)
        websocket = _RecordingWebSocket()
        server._register_client(websocket, websocket.remote_address)

        class Recorder:
            def __init__(self, **_kwargs):
                self.recording = False

            def start_session(self, _task, _description):
                return {
                    "success": True,
                    "next_episode_id": 3,
                    "message": "session started",
                }

            def start_recording(self):
                self.recording = True
                return {
                    "success": True,
                    "episode_id": 3,
                    "message": "recording started",
                }

            def stop_recording(self):
                was_recording = self.recording
                self.recording = False
                return {
                    "success": was_recording,
                    "episode_id": 3,
                    "frames": 10,
                    "message": "recording stopped",
                }

            def end_session(self):
                return {"success": True, "message": "session ended"}

        async def scenario() -> None:
            recorder_module = ModuleType("src.data_collection.recorder")
            recorder_module.DemonstrationRecorder = Recorder
            config_module = ModuleType("src.data_collection.config")

            class FakeDataCollectionConfig:
                @classmethod
                def from_settings(cls, _settings):
                    return cls()

            config_module.DataCollectionConfig = FakeDataCollectionConfig
            with patch.dict(
                sys.modules,
                {
                    "src.data_collection.config": config_module,
                    "src.data_collection.recorder": recorder_module,
                },
            ):
                await server._teleoperation_handler._handle_demo_session_start(
                    websocket,
                    {"task": "pick", "description": "test"},
                )
                self.assertIsNotNone(services.resources.owner_of(CAMERA))

                services.trajectory_teaching.start("left")
                await server._teleoperation_handler._handle_demo_record_start(websocket, {})
                self.assertEqual(
                    DataCollectionState.SESSION_READY,
                    services.data_collection.snapshot().state,
                )
                self.assertFalse(services.teleoperation.active)
                services.trajectory_teaching.cancel()

                await server._teleoperation_handler._handle_demo_record_start(websocket, {})
                self.assertEqual(
                    DataCollectionState.RECORDING,
                    services.data_collection.snapshot().state,
                )
                self.assertTrue(services.teleoperation.active)
                self.assertEqual(
                    "teleoperation",
                    services.resources.owner_of(ROBOT_SYSTEM),
                )
                await server._teleoperation_handler._handle_teleop_stop(
                    websocket,
                    {},
                )
                self.assertTrue(services.teleoperation.active)
                self.assertEqual(
                    DataCollectionState.RECORDING,
                    services.data_collection.snapshot().state,
                )

                await server._teleoperation_handler._handle_demo_record_stop(websocket, {})
                self.assertEqual(
                    DataCollectionState.SESSION_READY,
                    services.data_collection.snapshot().state,
                )
                self.assertTrue(services.teleoperation.active)
                await server._teleoperation_handler._handle_demo_session_end(websocket, {})
                self.assertEqual(
                    DataCollectionState.IDLE,
                    services.data_collection.snapshot().state,
                )

            self.assertIsNone(services.resources.owner_of(CAMERA))
            self.assertFalse(services.teleoperation.active)

        asyncio.run(scenario())
        self.assertFalse(services.devices.shutdown_all())

    def test_device_shutdown_closes_active_data_collection_session(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )

        class Recorder:
            def start_session(self, _task, _description):
                return {
                    "success": True,
                    "next_episode_id": 0,
                    "message": "session started",
                }

            def start_recording(self):
                return {
                    "success": True,
                    "episode_id": 0,
                    "message": "recording started",
                }

            def stop_recording(self):
                return {
                    "success": True,
                    "episode_id": 0,
                    "frames": 1,
                    "message": "recording stopped",
                }

            def end_session(self):
                return {"success": True, "message": "session ended"}

        recorder_module = ModuleType("src.data_collection.recorder")
        recorder_module.DemonstrationRecorder = lambda **_kwargs: Recorder()
        config_module = ModuleType("src.data_collection.config")

        class FakeDataCollectionConfig:
            @classmethod
            def from_settings(cls, _settings):
                return cls()

        config_module.DataCollectionConfig = FakeDataCollectionConfig
        with patch.dict(
            sys.modules,
            {
                "src.data_collection.config": config_module,
                "src.data_collection.recorder": recorder_module,
            },
        ):
            services.data_collection.start_session("pick")
            services.data_collection.start_episode()

        self.assertTrue(services.teleoperation.active)
        self.assertIsNotNone(services.resources.owner_of(CAMERA))

        self.assertFalse(services.devices.shutdown_all())
        self.assertEqual(
            DataCollectionState.IDLE,
            services.data_collection.snapshot().state,
        )
        self.assertFalse(services.teleoperation.active)
        self.assertIsNone(services.resources.owner_of(CAMERA))


class _RecordingWebSocket:
    remote_address = ("test", 1)

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


if __name__ == "__main__":
    unittest.main()
