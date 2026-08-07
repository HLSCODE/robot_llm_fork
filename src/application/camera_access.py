from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Generic, TypeGuard, TypeVar
from uuid import uuid4

from ..devices import (
    CameraSource,
    DepthCameraSource,
    DeviceContractError,
    DeviceRuntime,
    ResourceArbiter,
    ResourceLease,
)
from ..devices.runtime.ids import CAMERA


CameraT = TypeVar("CameraT", bound=CameraSource)


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
    ) -> None:
        self._runtime = runtime
        self._resources = resources

    def open(self, purpose: str) -> CameraSession[CameraSource]:
        return self._open(
            purpose,
            _is_camera_source,
            expected_contract="CameraSource",
        )

    def open_depth(
        self,
        purpose: str,
    ) -> CameraSession[DepthCameraSource]:
        return self._open(
            purpose,
            _is_depth_camera_source,
            expected_contract="DepthCameraSource",
        )

    def status(self) -> CameraStatus:
        """Return a presentation-safe camera snapshot without exposing runtime."""
        camera = self._runtime.get_if_ready(CAMERA)
        if camera is None or not isinstance(camera, CameraSource):
            return CameraStatus(False, 0, ())
        camera_count = camera.camera_count
        return CameraStatus(
            available=camera_count > 0,
            camera_count=camera_count,
            cameras=tuple(
                dict(camera_info)
                for camera_info in camera.get_cameras_info()
            ),
        )

    def _open(
        self,
        purpose: str,
        validator: Callable[[object], TypeGuard[CameraT]],
        *,
        expected_contract: str,
    ) -> CameraSession[CameraT]:
        normalized_purpose = purpose.strip()
        if not normalized_purpose:
            raise ValueError("camera session purpose must not be empty")

        lease = self._resources.acquire(
            owner_id=(
                f"camera:{normalized_purpose}:{uuid4().hex}"
            ),
            resources=(CAMERA,),
        )
        try:
            camera_instance: object = self._runtime.require(CAMERA)
            if not validator(camera_instance):
                raise DeviceContractError(
                    f"device '{CAMERA}' does not implement {expected_contract}"
                )
        except Exception:
            lease.release()
            raise
        return CameraSession(camera=camera_instance, _lease=lease)


def _is_camera_source(value: object) -> TypeGuard[CameraSource]:
    return isinstance(value, CameraSource)


def _is_depth_camera_source(value: object) -> TypeGuard[DepthCameraSource]:
    return isinstance(value, DepthCameraSource)
