from __future__ import annotations

import unittest

from src.application import (
    DataCollectionError,
    DataCollectionErrorCode,
    DataCollectionService,
    DataCollectionState,
)
from src.device_runtime.ids import ROBOT_SYSTEM


class _FakeCameraSession:
    def __init__(self) -> None:
        self.camera = object()
        self.active = True
        self.close_count = 0

    def close(self) -> None:
        self.active = False
        self.close_count += 1


class _FakeCameraAccess:
    def __init__(self) -> None:
        self.sessions: list[_FakeCameraSession] = []
        self.purposes: list[str] = []

    def open_depth(self, purpose: str) -> _FakeCameraSession:
        session = _FakeCameraSession()
        self.sessions.append(session)
        self.purposes.append(purpose)
        return session


class _FakeDevices:
    def __init__(self) -> None:
        self.initialized: list[str] = []

    def initialize(self, device_id: str) -> None:
        self.initialized.append(device_id)


class _FakeRobotQuery:
    reader = object()

    def state_reader(self):
        return self.reader


class _FakeTeleoperation:
    def __init__(self) -> None:
        self.active = False
        self.start_count = 0
        self.stop_count = 0
        self.start_error: Exception | None = None

    def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error
        self.active = True

    def stop(self) -> None:
        self.stop_count += 1
        self.active = False


class _FakeRecorder:
    def __init__(self) -> None:
        self.next_episode_id = 3
        self.recording = False
        self.start_session_success = True
        self.start_recording_success = True
        self.stop_recording_success = True
        self.stop_frames: object = 10
        self.end_session_success = True
        self.stop_count = 0
        self.end_count = 0

    def start_session(self, _task: str, _description: str):
        return {
            "success": self.start_session_success,
            "next_episode_id": self.next_episode_id,
            "message": "session started",
        }

    def start_recording(self):
        if not self.start_recording_success:
            return {
                "success": False,
                "message": "recording rejected",
            }
        self.recording = True
        return {
            "success": True,
            "episode_id": self.next_episode_id,
            "message": "recording started",
        }

    def stop_recording(self):
        self.stop_count += 1
        episode_id = self.next_episode_id
        self.recording = False
        if self.stop_recording_success:
            self.next_episode_id += 1
        return {
            "success": self.stop_recording_success,
            "episode_id": episode_id,
            "frames": self.stop_frames,
            "message": (
                "recording stopped" if self.stop_recording_success else "save failed"
            ),
        }

    def end_session(self):
        self.end_count += 1
        return {
            "success": self.end_session_success,
            "message": (
                "session ended" if self.end_session_success else "session end failed"
            ),
        }


class DataCollectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.camera_access = _FakeCameraAccess()
        self.devices = _FakeDevices()
        self.robot_query = _FakeRobotQuery()
        self.teleoperation = _FakeTeleoperation()
        self.recorder = _FakeRecorder()
        self.service = DataCollectionService(
            camera_access=self.camera_access,
            devices=self.devices,
            robot_query=self.robot_query,
            teleoperation=self.teleoperation,
            recorder_factory=lambda _reader, _camera: self.recorder,
        )

    def test_session_episode_lifecycle_has_explicit_states_and_results(self):
        self.assertEqual(
            DataCollectionState.IDLE,
            self.service.snapshot().state,
        )

        session = self.service.start_session("pick", "test task")
        self.assertEqual(3, session.next_episode_id)
        self.assertEqual([ROBOT_SYSTEM], self.devices.initialized)
        self.assertEqual(["data-collection"], self.camera_access.purposes)
        self.assertEqual(
            DataCollectionState.SESSION_READY,
            self.service.snapshot().state,
        )

        episode = self.service.start_episode()
        self.assertEqual(3, episode.episode_id)
        recording = self.service.snapshot()
        self.assertEqual(DataCollectionState.RECORDING, recording.state)
        self.assertTrue(recording.teleoperation_shared)
        self.assertTrue(self.teleoperation.active)

        stopped = self.service.stop_episode()
        self.assertEqual(3, stopped.episode_id)
        self.assertEqual(10, stopped.frames)
        ready = self.service.snapshot()
        self.assertEqual(DataCollectionState.SESSION_READY, ready.state)
        self.assertEqual(4, ready.next_episode_id)
        self.assertTrue(self.teleoperation.active)

        second_episode = self.service.start_episode()
        self.assertEqual(4, second_episode.episode_id)
        ended = self.service.end_session()

        self.assertEqual("session ended", ended.message)
        self.assertEqual(DataCollectionState.IDLE, self.service.snapshot().state)
        self.assertFalse(self.teleoperation.active)
        self.assertFalse(self.camera_access.sessions[0].active)
        self.assertEqual(2, self.recorder.stop_count)

    def test_invalid_transition_is_rejected_without_allocating_resources(self):
        with self.assertRaises(DataCollectionError) as raised:
            self.service.start_episode()

        self.assertEqual(
            DataCollectionErrorCode.INVALID_STATE,
            raised.exception.code,
        )
        self.assertFalse(self.camera_access.sessions)
        self.assertFalse(self.teleoperation.active)

    def test_session_start_failure_cleans_recorder_and_camera(self):
        self.recorder.start_session_success = False

        with self.assertRaises(DataCollectionError) as raised:
            self.service.start_session("pick")

        self.assertEqual(
            DataCollectionErrorCode.SESSION_START_FAILED,
            raised.exception.code,
        )
        self.assertEqual(DataCollectionState.IDLE, self.service.snapshot().state)
        self.assertEqual(1, self.recorder.end_count)
        self.assertFalse(self.camera_access.sessions[0].active)

    def test_episode_start_failure_releases_new_teleoperation_session(self):
        self.service.start_session("pick")
        self.recorder.start_recording_success = False

        with self.assertRaises(DataCollectionError) as raised:
            self.service.start_episode()

        self.assertEqual(
            DataCollectionErrorCode.EPISODE_START_FAILED,
            raised.exception.code,
        )
        self.assertEqual(
            DataCollectionState.SESSION_READY,
            self.service.snapshot().state,
        )
        self.assertFalse(self.teleoperation.active)
        self.assertEqual(1, self.teleoperation.stop_count)

    def test_save_failure_returns_structured_details_and_keeps_session_usable(self):
        self.service.start_session("pick")
        self.service.start_episode()
        self.recorder.stop_recording_success = False
        self.recorder.stop_frames = 7

        with self.assertRaises(DataCollectionError) as raised:
            self.service.stop_episode()

        self.assertEqual(
            DataCollectionErrorCode.EPISODE_STOP_FAILED,
            raised.exception.code,
        )
        self.assertEqual(3, raised.exception.episode_id)
        self.assertEqual(7, raised.exception.frames)
        self.assertEqual(
            DataCollectionState.SESSION_READY,
            self.service.snapshot().state,
        )
        self.assertTrue(self.teleoperation.active)

        self.service.end_session()
        self.assertFalse(self.teleoperation.active)

    def test_malformed_recorder_result_faults_then_end_cleans_all_resources(self):
        self.service.start_session("pick")
        self.service.start_episode()
        self.recorder.stop_frames = "not-an-integer"

        with self.assertRaises(DataCollectionError) as raised:
            self.service.stop_episode()

        self.assertEqual(
            DataCollectionErrorCode.RECORDER_PROTOCOL_ERROR,
            raised.exception.code,
        )
        self.assertEqual(
            DataCollectionState.FAULTED,
            self.service.snapshot().state,
        )

        self.service.end_session()

        self.assertEqual(DataCollectionState.IDLE, self.service.snapshot().state)
        self.assertFalse(self.teleoperation.active)
        self.assertFalse(self.camera_access.sessions[0].active)

    def test_close_is_idempotent_and_releases_an_active_episode(self):
        self.service.start_session("pick")
        self.service.start_episode()

        self.service.close()
        self.service.close()

        self.assertEqual(DataCollectionState.IDLE, self.service.snapshot().state)
        self.assertFalse(self.recorder.recording)
        self.assertFalse(self.teleoperation.active)
        self.assertEqual(1, self.camera_access.sessions[0].close_count)


if __name__ == "__main__":
    unittest.main()
