from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Generic, TypeVar
from uuid import uuid4

from ..devices import (
    CameraSource,
    DepthCameraSource,
    DeviceRuntime,
    ResourceArbiter,
    ResourceLease,
)
from ..devices.runtime.ids import CAMERA


CameraT = TypeVar("CameraT", bound=CameraSource)


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
        return self._open(purpose, CameraSource)

    def open_depth(
        self,
        purpose: str,
    ) -> CameraSession[DepthCameraSource]:
        return self._open(purpose, DepthCameraSource)

    def _open(
        self,
        purpose: str,
        expected_type: type[CameraT],
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
            camera = self._runtime.require(CAMERA, expected_type)
        except Exception:
            lease.release()
            raise
        return CameraSession(camera=camera, _lease=lease)
