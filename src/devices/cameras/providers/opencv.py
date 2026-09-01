"""OpenCV camera provider."""

from __future__ import annotations

from ....configuration.settings import VisionSettings
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..opencv_manager import OpenCVCameraManager, probe_opencv_cameras
from ..provider import CameraProviderDefinition

def create_opencv_camera(settings: VisionSettings) -> OpenCVCameraManager:
    profiles = settings.camera_profiles_for_provider("opencv")
    if not profiles:
        raise DeviceInitializationError(
            "OpenCV provider requires at least one [[vision.cameras]] profile"
        )
    try:
        cameras = [
            {
                "index": int(profile.device_id),
                "name": profile.name,
            }
            for profile in profiles
        ]
    except ValueError as exc:
        raise DeviceInitializationError(
            "OpenCV camera device_id must be an integer device index"
        ) from exc
    manager = OpenCVCameraManager(
        cameras=cameras,
        fps=settings.webcam_fps,
        width=settings.webcam_width,
        height=settings.webcam_height,
        jpeg_quality=settings.webcam_jpeg_quality,
        encode_fps=settings.camera_encode_fps,
    )
    return manager


def probe_opencv_provider(
    settings: VisionSettings,
    timeout_seconds: float,
    max_attempts: int,
) -> tuple[dict[str, object], ...]:
    profiles = settings.camera_profiles_for_provider("opencv")
    return probe_opencv_cameras(
        tuple({"index": profile.device_id, "name": profile.name} for profile in profiles),
        width=settings.webcam_width,
        height=settings.webcam_height,
        fps=settings.webcam_fps,
        backend=None,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )


OPENCV_CAMERA_PROVIDER = CameraProviderDefinition(
    name="opencv",
    capabilities=frozenset({DeviceCapability.CAMERA}),
    create=create_opencv_camera,
    probe=probe_opencv_provider,
)

__all__ = ["OPENCV_CAMERA_PROVIDER", "create_opencv_camera"]
