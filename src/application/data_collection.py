from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Any, Protocol

from ..devices import ArmId, ArmTelemetryReader, DepthCameraSource
from ..devices.runtime.ids import ROBOT_SYSTEM
from .camera_access import CameraAccessService, CameraSession
from .teleoperation import DATA_COLLECTION_TELEOPERATION_OWNER


class _DeviceManagementPort(Protocol):
    def initialize(self, device_id: str) -> object: ...


class _RobotQueryPort(Protocol):
    def telemetry_reader(self) -> ArmTelemetryReader: ...


class _TeleoperationPort(Protocol):
    @property
    def active(self) -> bool: ...

    def start(
        self,
        owner_id: str,
        arms: tuple[ArmId, ...],
    ) -> object: ...

    def stop(self, owner_id: str) -> object: ...


class DataCollectionState(str, Enum):
    """Explicit lifecycle states for one data-collection session."""

    IDLE = "idle"
    STARTING_SESSION = "starting_session"
    SESSION_READY = "session_ready"
    STARTING_EPISODE = "starting_episode"
    RECORDING = "recording"
    STOPPING_EPISODE = "stopping_episode"
    ENDING_SESSION = "ending_session"
    FAULTED = "faulted"
    CLOSING = "closing"


class DataCollectionErrorCode(str, Enum):
    INVALID_STATE = "invalid_state"
    SESSION_START_FAILED = "session_start_failed"
    EPISODE_START_FAILED = "episode_start_failed"
    EPISODE_STOP_FAILED = "episode_stop_failed"
    SESSION_END_FAILED = "session_end_failed"
    RECORDER_PROTOCOL_ERROR = "recorder_protocol_error"
    CLEANUP_FAILED = "cleanup_failed"
    INSUFFICIENT_STORAGE = "insufficient_storage"
    EPISODE_CONFLICT = "episode_conflict"
    DATA_INTEGRITY_FAILED = "data_integrity_failed"
    FORMAT_UNAVAILABLE = "format_unavailable"
    PERSISTENCE_FAILED = "persistence_failed"


_RECORDER_PERSISTENCE_ERROR_CODES = frozenset(
    {
        DataCollectionErrorCode.INSUFFICIENT_STORAGE,
        DataCollectionErrorCode.EPISODE_CONFLICT,
        DataCollectionErrorCode.DATA_INTEGRITY_FAILED,
        DataCollectionErrorCode.FORMAT_UNAVAILABLE,
        DataCollectionErrorCode.PERSISTENCE_FAILED,
    }
)


class DataCollectionError(RuntimeError):
    """Stable application error raised by data-collection use cases."""

    def __init__(
        self,
        code: DataCollectionErrorCode,
        message: str,
        *,
        episode_id: int | None = None,
        frames: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.episode_id = episode_id
        self.frames = frames


class DataCollectionRecorder(Protocol):
    """Recorder boundary implemented by data-collection infrastructure."""

    def start_session(
        self,
        task: str,
        description: str,
    ) -> Mapping[str, Any]: ...

    def start_recording(self) -> Mapping[str, Any]: ...

    def stop_recording(self) -> Mapping[str, Any]: ...

    def end_session(self) -> Mapping[str, Any]: ...


DataCollectionRecorderFactory = Callable[
    [ArmTelemetryReader, DepthCameraSource],
    DataCollectionRecorder,
]


@dataclass(frozen=True, slots=True)
class DataCollectionSnapshot:
    state: DataCollectionState
    task: str | None
    description: str | None
    next_episode_id: int | None
    episode_id: int | None
    teleoperation_shared: bool

    @property
    def session_active(self) -> bool:
        return self.state is not DataCollectionState.IDLE

    @property
    def recording(self) -> bool:
        return self.state in {
            DataCollectionState.STARTING_EPISODE,
            DataCollectionState.RECORDING,
            DataCollectionState.STOPPING_EPISODE,
        }


@dataclass(frozen=True, slots=True)
class DataCollectionSessionStarted:
    task: str
    next_episode_id: int
    message: str


@dataclass(frozen=True, slots=True)
class DataCollectionEpisodeStarted:
    episode_id: int
    message: str


@dataclass(frozen=True, slots=True)
class DataCollectionEpisodeStopped:
    episode_id: int
    frames: int
    message: str


@dataclass(frozen=True, slots=True)
class DataCollectionSessionEnded:
    message: str


class DataCollectionService:
    """Own recorder, camera and shared teleoperation session lifecycles."""

    def __init__(
        self,
        *,
        camera_access: CameraAccessService,
        devices: _DeviceManagementPort,
        robot_query: _RobotQueryPort,
        teleoperation: _TeleoperationPort,
        recorder_factory: DataCollectionRecorderFactory,
    ) -> None:
        self._camera_access = camera_access
        self._devices = devices
        self._robot_query = robot_query
        self._teleoperation = teleoperation
        self._recorder_factory = recorder_factory

        self._state = DataCollectionState.IDLE
        self._task: str | None = None
        self._description: str | None = None
        self._next_episode_id: int | None = None
        self._episode_id: int | None = None
        self._teleoperation_shared = False
        self._recorder_may_be_recording = False
        self._recorder: DataCollectionRecorder | None = None
        self._camera_session: CameraSession[DepthCameraSource] | None = None

        self._state_lock = RLock()
        self._operation_lock = RLock()

    def snapshot(self) -> DataCollectionSnapshot:
        with self._state_lock:
            return DataCollectionSnapshot(
                state=self._state,
                task=self._task,
                description=self._description,
                next_episode_id=self._next_episode_id,
                episode_id=self._episode_id,
                teleoperation_shared=self._teleoperation_shared,
            )

    def start_session(
        self,
        task: str,
        description: str = "",
    ) -> DataCollectionSessionStarted:
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("data collection task must not be empty")
        normalized_description = description.strip()

        with self._operation_lock:
            self._begin_transition(
                {DataCollectionState.IDLE},
                DataCollectionState.STARTING_SESSION,
                "start data collection session",
            )
            recorder: DataCollectionRecorder | None = None
            camera_session: CameraSession[DepthCameraSource] | None = None
            try:
                camera_session = self._camera_access.open_depth("data-collection")
                self._devices.initialize(ROBOT_SYSTEM)
                recorder = self._recorder_factory(
                    self._robot_query.telemetry_reader(),
                    camera_session.camera,
                )
                result = self._successful_result(
                    recorder.start_session(
                        normalized_task,
                        normalized_description,
                    ),
                    DataCollectionErrorCode.SESSION_START_FAILED,
                    "data collection session start",
                )
                next_episode_id = self._required_nonnegative_int(
                    result,
                    "next_episode_id",
                )
                message = self._message(
                    result,
                    (f"数据采集会话已启动，下一个 episode 编号为 {next_episode_id}"),
                )
            except Exception as exc:
                cleanup_errors = self._cleanup_failed_session(
                    recorder,
                    camera_session,
                )
                self._reset()
                raise self._operation_error(
                    exc,
                    DataCollectionErrorCode.SESSION_START_FAILED,
                    "数据采集会话启动失败",
                    cleanup_errors,
                ) from exc

            with self._state_lock:
                self._recorder = recorder
                self._camera_session = camera_session
                self._task = normalized_task
                self._description = normalized_description
                self._next_episode_id = next_episode_id
                self._episode_id = None
                self._teleoperation_shared = False
                self._recorder_may_be_recording = False
                self._state = DataCollectionState.SESSION_READY
            return DataCollectionSessionStarted(
                task=normalized_task,
                next_episode_id=next_episode_id,
                message=message,
            )

    def start_episode(self) -> DataCollectionEpisodeStarted:
        with self._operation_lock:
            self._begin_transition(
                {DataCollectionState.SESSION_READY},
                DataCollectionState.STARTING_EPISODE,
                "start data collection episode",
            )
            recorder = self._require_recorder()
            recorder_start_invoked = False
            try:
                self._teleoperation.start(
                    DATA_COLLECTION_TELEOPERATION_OWNER,
                    (ArmId.LEFT, ArmId.RIGHT),
                )
                recorder_start_invoked = True
                raw_result = recorder.start_recording()
                if (
                    isinstance(raw_result, Mapping)
                    and raw_result.get("success") is True
                ):
                    with self._state_lock:
                        self._recorder_may_be_recording = True
                result = self._successful_result(
                    raw_result,
                    DataCollectionErrorCode.EPISODE_START_FAILED,
                    "data collection episode start",
                )
                episode_id = self._required_nonnegative_int(
                    result,
                    "episode_id",
                )
                message = self._message(
                    result,
                    f"episode {episode_id} 开始记录",
                )
            except Exception as exc:
                cleanup_errors: list[str] = []
                if recorder_start_invoked:
                    try:
                        recorder.stop_recording()
                        with self._state_lock:
                            self._recorder_may_be_recording = False
                    except Exception as cleanup_exc:
                        cleanup_errors.append(f"stop recorder: {cleanup_exc}")
                try:
                    self._teleoperation.stop(
                        DATA_COLLECTION_TELEOPERATION_OWNER
                    )
                except Exception as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
                self._set_state(
                    DataCollectionState.FAULTED
                    if cleanup_errors
                    else DataCollectionState.SESSION_READY
                )
                raise self._operation_error(
                    exc,
                    DataCollectionErrorCode.EPISODE_START_FAILED,
                    "数据采集 episode 启动失败",
                    cleanup_errors,
                ) from exc

            with self._state_lock:
                self._episode_id = episode_id
                self._teleoperation_shared = True
                self._state = DataCollectionState.RECORDING
            return DataCollectionEpisodeStarted(
                episode_id=episode_id,
                message=message,
            )

    def stop_episode(self) -> DataCollectionEpisodeStopped:
        with self._operation_lock:
            self._begin_transition(
                {DataCollectionState.RECORDING},
                DataCollectionState.STOPPING_EPISODE,
                "stop data collection episode",
            )
            recorder = self._require_recorder()
            try:
                raw_result = recorder.stop_recording()
                with self._state_lock:
                    self._recorder_may_be_recording = False
                result = self._successful_result(
                    raw_result,
                    DataCollectionErrorCode.EPISODE_STOP_FAILED,
                    "data collection episode stop",
                )
                episode_id = self._required_nonnegative_int(
                    result,
                    "episode_id",
                )
                frames = self._required_nonnegative_int(result, "frames")
                message = self._message(
                    result,
                    f"episode {episode_id} 已保存，共 {frames} 帧",
                )
            except DataCollectionError as exc:
                next_state = (
                    DataCollectionState.FAULTED
                    if exc.code is DataCollectionErrorCode.RECORDER_PROTOCOL_ERROR
                    else DataCollectionState.SESSION_READY
                )
                with self._state_lock:
                    self._episode_id = None
                    self._state = next_state
                raise
            except Exception as exc:
                self._set_state(DataCollectionState.FAULTED)
                raise DataCollectionError(
                    DataCollectionErrorCode.EPISODE_STOP_FAILED,
                    f"数据采集 episode 停止失败: {exc}",
                    episode_id=self.snapshot().episode_id,
                ) from exc

            with self._state_lock:
                self._episode_id = None
                current_next = self._next_episode_id or 0
                self._next_episode_id = max(
                    current_next,
                    episode_id + 1,
                )
                self._state = DataCollectionState.SESSION_READY
            return DataCollectionEpisodeStopped(
                episode_id=episode_id,
                frames=frames,
                message=message,
            )

    def end_session(self) -> DataCollectionSessionEnded:
        with self._operation_lock:
            self._begin_transition(
                {
                    DataCollectionState.SESSION_READY,
                    DataCollectionState.RECORDING,
                    DataCollectionState.FAULTED,
                },
                DataCollectionState.ENDING_SESSION,
                "end data collection session",
            )
            recorder = self._require_recorder()
            camera_session = self._camera_session
            errors: list[str] = []
            message = "数据采集会话已结束"

            if self._recorder_may_be_recording:
                self._record_cleanup_result(
                    errors,
                    "stop episode",
                    recorder.stop_recording,
                )
            try:
                result = self._successful_result(
                    recorder.end_session(),
                    DataCollectionErrorCode.SESSION_END_FAILED,
                    "data collection session end",
                )
                message = self._message(result, message)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                self._release_owned_resources(camera_session, errors)
                self._reset()

            if errors:
                raise DataCollectionError(
                    DataCollectionErrorCode.SESSION_END_FAILED,
                    "结束数据采集会话失败: " + "; ".join(errors),
                )
            return DataCollectionSessionEnded(message=message)

    def close(self) -> None:
        """Best-effort, idempotent cleanup for host shutdown or lease loss."""

        with self._operation_lock:
            previous_state = self.snapshot().state
            if previous_state is DataCollectionState.IDLE:
                return
            self._set_state(DataCollectionState.CLOSING)
            recorder = self._recorder
            camera_session = self._camera_session
            errors: list[str] = []

            if recorder is not None:
                if self._recorder_may_be_recording:
                    self._record_cleanup_result(
                        errors,
                        "stop episode",
                        recorder.stop_recording,
                    )
                self._record_cleanup_result(
                    errors,
                    "end session",
                    recorder.end_session,
                )
            self._release_owned_resources(camera_session, errors)
            self._reset()

            if errors:
                raise DataCollectionError(
                    DataCollectionErrorCode.CLEANUP_FAILED,
                    "清理数据采集会话失败: " + "; ".join(errors),
                )

    def _begin_transition(
        self,
        expected: set[DataCollectionState],
        target: DataCollectionState,
        operation: str,
    ) -> None:
        with self._state_lock:
            if self._state not in expected:
                expected_names = ", ".join(sorted(state.value for state in expected))
                raise DataCollectionError(
                    DataCollectionErrorCode.INVALID_STATE,
                    (
                        f"cannot {operation} while state is "
                        f"{self._state.value}; expected {expected_names}"
                    ),
                )
            self._state = target

    def _require_recorder(self) -> DataCollectionRecorder:
        with self._state_lock:
            recorder = self._recorder
        if recorder is None:
            raise DataCollectionError(
                DataCollectionErrorCode.RECORDER_PROTOCOL_ERROR,
                "data collection recorder is unavailable",
            )
        return recorder

    def _release_owned_resources(
        self,
        camera_session: CameraSession[DepthCameraSource] | None,
        errors: list[str],
    ) -> None:
        if self._teleoperation_shared:
            try:
                self._teleoperation.stop(
                    DATA_COLLECTION_TELEOPERATION_OWNER
                )
            except Exception as exc:
                errors.append(f"stop teleoperation: {exc}")
        if camera_session is not None:
            try:
                camera_session.close()
            except Exception as exc:
                errors.append(f"close camera session: {exc}")

    @classmethod
    def _successful_result(
        cls,
        result: Mapping[str, Any],
        failure_code: DataCollectionErrorCode,
        operation: str,
    ) -> Mapping[str, Any]:
        if not isinstance(result, Mapping):
            raise DataCollectionError(
                DataCollectionErrorCode.RECORDER_PROTOCOL_ERROR,
                f"{operation} returned a non-mapping result",
            )
        if result.get("success") is not True:
            error_code = failure_code
            raw_error_code = result.get("error_code")
            if isinstance(raw_error_code, str):
                try:
                    candidate = DataCollectionErrorCode(raw_error_code)
                except ValueError:
                    candidate = failure_code
                if candidate in _RECORDER_PERSISTENCE_ERROR_CODES:
                    error_code = candidate
            raise DataCollectionError(
                error_code,
                cls._message(result, f"{operation} was rejected"),
                episode_id=cls._optional_nonnegative_int(
                    result,
                    "episode_id",
                ),
                frames=cls._optional_nonnegative_int(result, "frames"),
            )
        return result

    @staticmethod
    def _required_nonnegative_int(
        result: Mapping[str, Any],
        field: str,
    ) -> int:
        value = result.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DataCollectionError(
                DataCollectionErrorCode.RECORDER_PROTOCOL_ERROR,
                (f"recorder result field '{field}' must be a non-negative integer"),
            )
        return value

    @staticmethod
    def _optional_nonnegative_int(
        result: Mapping[str, Any],
        field: str,
    ) -> int | None:
        value = result.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _message(result: Mapping[str, Any], default: str) -> str:
        message = result.get("message")
        return (
            message.strip() if isinstance(message, str) and message.strip() else default
        )

    @staticmethod
    def _record_cleanup_result(
        errors: list[str],
        operation: str,
        callback: Callable[[], Mapping[str, Any]],
    ) -> None:
        try:
            raw_result: object = callback()
            if not isinstance(raw_result, Mapping):
                errors.append(f"{operation}: recorder returned a non-mapping result")
            elif raw_result.get("success") is not True:
                errors.append(
                    f"{operation}: "
                    f"{DataCollectionService._message(raw_result, 'operation failed')}"
                )
        except Exception as exc:
            errors.append(f"{operation}: {exc}")

    @staticmethod
    def _cleanup_failed_session(
        recorder: DataCollectionRecorder | None,
        camera_session: CameraSession[DepthCameraSource] | None,
    ) -> list[str]:
        errors: list[str] = []
        if recorder is not None:
            DataCollectionService._record_cleanup_result(
                errors,
                "end failed session",
                recorder.end_session,
            )
        if camera_session is not None:
            try:
                camera_session.close()
            except Exception as exc:
                errors.append(f"close camera session: {exc}")
        return errors

    @staticmethod
    def _operation_error(
        error: Exception,
        default_code: DataCollectionErrorCode,
        prefix: str,
        cleanup_errors: list[str],
    ) -> DataCollectionError:
        if isinstance(error, DataCollectionError):
            code = error.code
            episode_id = error.episode_id
            frames = error.frames
            message = str(error)
        else:
            code = default_code
            episode_id = None
            frames = None
            message = f"{prefix}: {error}"
        if cleanup_errors:
            message += "; cleanup: " + "; ".join(cleanup_errors)
        return DataCollectionError(
            code,
            message,
            episode_id=episode_id,
            frames=frames,
        )

    def _set_state(self, state: DataCollectionState) -> None:
        with self._state_lock:
            self._state = state

    def _reset(self) -> None:
        with self._state_lock:
            self._state = DataCollectionState.IDLE
            self._task = None
            self._description = None
            self._next_episode_id = None
            self._episode_id = None
            self._teleoperation_shared = False
            self._recorder_may_be_recording = False
            self._recorder = None
            self._camera_session = None
