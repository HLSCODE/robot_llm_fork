from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from src.application import CameraAccessService, create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.device_runtime import (
    DeviceCapability,
    DeviceContractError,
    DeviceNotRegisteredError,
    DeviceRegistration,
    DeviceRuntime,
    ResourceArbiter,
    ResourceBusyError,
)
from src.device_runtime.ids import CAMERA, ROBOT_SYSTEM
from src.execution import ExecutionState
from src.robot_server.ws_server import RobotWebSocketServer
from src.voice_interaction import CameraCaptureError, CamerasModuleProvider


class CameraAccessServiceTests(unittest.TestCase):
    def test_camera_session_only_blocks_sequences_that_need_camera(self):
        services = create_application_services(object(), simulation=True)
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
        self.assertTrue(
            services.resources.owner_of(CAMERA).startswith("camera:test:")
        )
        wait_result = services.execution.start(
            [wait_item],
            origin="test",
        ).wait(1)
        self.assertEqual(ExecutionState.SUCCEEDED, wait_result.state)
        with self.assertRaises(ResourceBusyError):
            services.execution.start([vision_item], origin="test")

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
        services = create_application_services(object(), simulation=True)
        session = services.camera_access.open("lifecycle-test")

        with self.assertRaises(ResourceBusyError):
            services.devices.initialize(CAMERA)
        with self.assertRaises(ResourceBusyError):
            services.devices.shutdown_all()

        session.close()
        self.assertFalse(services.devices.shutdown_all())

    def test_failed_device_initialization_releases_lifecycle_lease(self):
        services = create_application_services(object(), simulation=True)

        with self.assertRaises(DeviceNotRegisteredError):
            services.devices.initialize("missing-device")

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
        services = create_application_services(object(), simulation=True)
        server = RobotWebSocketServer(services)
        websocket = _RecordingWebSocket()

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
        services = create_application_services(object(), simulation=True)
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
        services = create_application_services(object(), simulation=True)
        server = RobotWebSocketServer(services)
        websocket = _RecordingWebSocket()

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
            data_collection_module = ModuleType("src.data_collection")
            data_collection_module.RLBenchRecorder = Recorder
            config_module = ModuleType("src.data_collection.config")
            config_module.DataCollectionConfig = type(
                "DataCollectionConfig",
                (),
                {},
            )
            with patch.dict(
                sys.modules,
                {
                    "src.data_collection": data_collection_module,
                    "src.data_collection.config": config_module,
                },
            ):
                await server._teleoperation_handler._handle_demo_session_start(
                    websocket,
                    {"task": "pick", "description": "test"},
                )
                self.assertIsNotNone(services.resources.owner_of(CAMERA))

                services.trajectory_teaching.start("left")
                await server._teleoperation_handler._handle_demo_record_start(websocket, {})
                self.assertFalse(server._demo_recorder.recording)
                self.assertFalse(services.teleoperation.active)
                services.trajectory_teaching.cancel()

                await server._teleoperation_handler._handle_demo_record_start(websocket, {})
                self.assertTrue(services.teleoperation.active)
                self.assertEqual(
                    "teleoperation",
                    services.resources.owner_of(ROBOT_SYSTEM),
                )

                await server._teleoperation_handler._handle_demo_record_stop(websocket, {})
                await server._teleoperation_handler._handle_demo_session_end(websocket, {})

            self.assertIsNone(services.resources.owner_of(CAMERA))
            self.assertFalse(services.teleoperation.active)

        asyncio.run(scenario())
        self.assertFalse(services.devices.shutdown_all())


class _RecordingWebSocket:
    remote_address = ("test", 1)

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


if __name__ == "__main__":
    unittest.main()
