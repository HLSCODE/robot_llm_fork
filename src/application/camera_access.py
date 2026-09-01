from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import logging
from threading import RLock, Timer
import time
from typing import Any, Generic, TypeGuard, TypeVar
from uuid import uuid4

from ..devices import (
    CameraSource,
    DepthCameraSource,
    DeviceContractError,
    DeviceRuntime,
    ResourceArbiter,
    ResourceLease,
    SelectableCameraSource,
)
from ..devices.runtime.ids import CAMERA


logger = logging.getLogger(__name__)
CameraT = TypeVar("CameraT", bound=CameraSource)
CameraProbe = Callable[[float, int], tuple[dict[str, object], ...]]


@dataclass(frozen=True, slots=True)
class CameraStatus:
    available: bool
    camera_count: int
    cameras: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "camera_count": self.camera_count,
            "cameras": [dict(camera) for camera in self.cameras],
        }


@dataclass(slots=True)
class CameraSession(Generic[CameraT]):
    """Exclusive camera access with an explicit, idempotent lifetime."""

    camera: CameraT
    _lease: ResourceLease
    _after_close: Callable[[], None] = field(repr=False)
    _closed: bool = False
    _lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._lease.release()
        self._after_close()

    def __enter__(self) -> CameraT:
        with self._lock:
            if self._closed:
                raise RuntimeError("camera session is closed")
            return self.camera

    def __exit__(self, *_args: object) -> None:
        self.close()


class CameraAccessService:
    """Create exclusive camera sessions through the shared resource arbiter."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        resources: ResourceArbiter,
        configured_cameras: tuple[dict[str, object], ...] = (),
        probe: CameraProbe | None = None,
        probe_timeout_seconds: float = 2.5,
        probe_max_attempts: int = 2,
        idle_timeout_seconds: float = 10.0,
    ) -> None:
        if probe_timeout_seconds <= 0:
            raise ValueError("camera probe timeout must be positive")
        if probe_max_attempts not in {1, 2}:
            raise ValueError("camera probe max attempts must be 1 or 2")
        if idle_timeout_seconds < 0:
            raise ValueError("camera idle timeout must not be negative")
        self._runtime = runtime
        self._resources = resources
        self._configured_cameras = tuple(dict(camera) for camera in configured_cameras)
        self._probe = probe
        self._probe_timeout_seconds = probe_timeout_seconds
        self._probe_max_attempts = probe_max_attempts
        self._idle_timeout_seconds = idle_timeout_seconds
        self._status_lock = RLock()
        self._last_status = CameraStatus(False, 0, ())
        self._lifecycle_lock = RLock()
        self._idle_timer: Timer | None = None

    def open(
        self,
        purpose: str,
        *,
        camera_names: Sequence[str] = (),
    ) -> CameraSession[CameraSource]:
        return self._open(
            purpose,
            _is_camera_source,
            expected_contract="CameraSource",
            camera_names=camera_names,
        )

    def open_depth(
        self,
        purpose: str,
        *,
        camera_names: Sequence[str] = (),
    ) -> CameraSession[DepthCameraSource]:
        return self._open(
            purpose,
            _is_depth_camera_source,
            expected_contract="DepthCameraSource",
            camera_names=camera_names,
        )

    def status(self) -> CameraStatus:
        """Return a presentation-safe camera snapshot without exposing runtime."""
        camera = self._runtime.get_if_ready(CAMERA)
        if camera is None or not isinstance(camera, CameraSource):
            with self._status_lock:
                return self._last_status
        status = self._status_from_runtime(camera)
        self._remember_status(status)
        return status

    def probe_all(self, *, frame_timeout_seconds: float | None = None) -> CameraStatus:
        """Start every configured camera, verify frames, then return to STOPPED."""
        timeout_seconds = (
            self._probe_timeout_seconds if frame_timeout_seconds is None else frame_timeout_seconds
        )
        if timeout_seconds <= 0:
            raise ValueError("camera frame timeout must be positive")
        lease = self._resources.acquire(
            owner_id=f"camera:health-check:{uuid4().hex}",
            resources=(CAMERA,),
        )
        try:
            self._cancel_idle_shutdown()
            self._runtime.shutdown(CAMERA)
            if self._probe is not None:
                try:
                    probe_cameras = self._probe(
                        timeout_seconds,
                        self._probe_max_attempts,
                    )
                except Exception as exc:
                    self._remember_status(self._failed_probe_status(str(exc)))
                    raise
                status = self._status_from_probe(probe_cameras)
                self._remember_status(status)
                self._log_unavailable(status)
                return status
            try:
                camera = self._runtime.require(CAMERA)
                status = _probe_camera_frames(camera, timeout_seconds)
                self._remember_status(status)
                self._log_unavailable(status)
                return status
            except Exception as exc:
                self._remember_status(self._failed_probe_status(str(exc)))
                raise
            finally:
                # Initialization failures can leave the runtime in FAILED. Shutdown
                # normalizes both READY and FAILED back to STOPPED, so later camera
                # consumers always start from a clean, idle state.
                self._runtime.shutdown(CAMERA)
        finally:
            lease.release()

    def _remember_status(self, status: CameraStatus) -> None:
        with self._status_lock:
            self._last_status = status

    def _failed_probe_status(self, error: str) -> CameraStatus:
        cameras = tuple(
            {
                **camera,
                "online": False,
                "frame_received": False,
                "error": error,
            }
            for camera in self._configured_cameras
        )
        return CameraStatus(False, 0, cameras)

    def _status_from_probe(
        self,
        probe_cameras: tuple[dict[str, object], ...],
    ) -> CameraStatus:
        configured = {str(camera.get("name", "")): camera for camera in self._configured_cameras}
        cameras = tuple(
            {
                **configured.get(str(camera.get("name", "")), {}),
                **camera,
            }
            for camera in probe_cameras
        )
        healthy_count = sum(bool(camera.get("frame_received")) for camera in cameras)
        return CameraStatus(healthy_count > 0, healthy_count, cameras)

    def _status_from_runtime(self, camera: CameraSource) -> CameraStatus:
        configured = {
            str(item.get("name", "")): item for item in self._configured_cameras
        }
        cameras = tuple(
            {
                **configured.get(str(item.get("name", "")), {}),
                **item,
                "frame_received": bool(item.get("online")),
            }
            for item in (dict(info) for info in camera.get_cameras_info())
        )
        online_count = sum(bool(item.get("online")) for item in cameras)
        return CameraStatus(online_count > 0, online_count, cameras)

    @staticmethod
    def _log_unavailable(status: CameraStatus) -> None:
        unavailable = [camera for camera in status.cameras if not camera.get("frame_received")]
        if unavailable:
            logger.warning(
                "Camera startup probe found unavailable cameras: %s",
                "; ".join(
                    f"{item.get('name', '?')}: {item.get('error', 'no frame received')}"
                    for item in unavailable
                ),
            )

    def activate_for_execution(
        self,
        camera_names: Sequence[str],
        *,
        require_depth: bool,
    ) -> CameraSource:
        """Activate selected pipelines while an execution lease is already held."""
        self._cancel_idle_shutdown()
        camera: object = self._runtime.require(CAMERA)
        _activate_selected(camera, camera_names)
        validator = _is_depth_camera_source if require_depth else _is_camera_source
        if not validator(camera):
            expected = "DepthCameraSource" if require_depth else "CameraSource"
            raise DeviceContractError(f"device '{CAMERA}' does not implement {expected}")
        return camera

    def defer_idle_shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            timer = Timer(self._idle_timeout_seconds, self._shutdown_if_idle)
            timer.daemon = True
            self._idle_timer = timer
            timer.start()

    def _cancel_idle_shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

    def _shutdown_if_idle(self) -> None:
        with self._lifecycle_lock:
            self._idle_timer = None
        if self._resources.owner_of(CAMERA) is not None:
            self.defer_idle_shutdown()
            return
        try:
            self._runtime.shutdown(CAMERA)
        except Exception:
            logger.exception("Camera idle shutdown failed")

    def _open(
        self,
        purpose: str,
        validator: Callable[[object], TypeGuard[CameraT]],
        *,
        expected_contract: str,
        camera_names: Sequence[str],
    ) -> CameraSession[CameraT]:
        normalized_purpose = purpose.strip()
        if not normalized_purpose:
            raise ValueError("camera session purpose must not be empty")

        lease = self._resources.acquire(
            owner_id=(f"camera:{normalized_purpose}:{uuid4().hex}"),
            resources=(CAMERA,),
        )
        try:
            self._cancel_idle_shutdown()
            camera_instance: object = self._runtime.require(CAMERA)
            _activate_selected(camera_instance, camera_names)
            if not validator(camera_instance):
                raise DeviceContractError(
                    f"device '{CAMERA}' does not implement {expected_contract}"
                )
        except Exception:
            lease.release()
            raise
        return CameraSession(
            camera=camera_instance,
            _lease=lease,
            _after_close=self.defer_idle_shutdown,
        )


def _is_camera_source(value: object) -> TypeGuard[CameraSource]:
    return isinstance(value, CameraSource)


def _is_depth_camera_source(value: object) -> TypeGuard[DepthCameraSource]:
    return isinstance(value, DepthCameraSource)


def _activate_selected(camera: object, camera_names: Sequence[str]) -> None:
    if not isinstance(camera, SelectableCameraSource):
        return
    result = camera.activate(tuple(camera_names))
    if int(result.get("started", 0)) <= 0:
        raise RuntimeError("no selected camera pipeline could be started")


def _probe_camera_frames(
    camera: CameraSource,
    timeout_seconds: float,
) -> CameraStatus:
    camera_info = [dict(info) for info in camera.get_cameras_info()]
    pending = {
        str(info.get("name", ""))
        for info in camera_info
        if info.get("online") and str(info.get("name", ""))
    }
    received: set[str] = set()
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        if isinstance(camera, DepthCameraSource):
            for name in tuple(pending):
                if camera.get_latest_depth_frame(name) is not None:
                    received.add(name)
                    pending.remove(name)
        else:
            frame_names = {name for _serial, name, _jpeg in camera.get_latest_jpegs()}
            received.update(pending & frame_names)
            pending.difference_update(frame_names)
        if pending:
            time.sleep(0.1)

    for info in camera_info:
        name = str(info.get("name", ""))
        frame_received = name in received
        info["frame_received"] = frame_received
        if info.get("online") and not frame_received:
            info["error"] = f"{timeout_seconds:g} 秒内未获得有效帧"
    healthy_count = sum(bool(info.get("frame_received")) for info in camera_info)
    return CameraStatus(
        available=healthy_count > 0,
        camera_count=healthy_count,
        cameras=tuple(camera_info),
    )
