"""OpenCV camera provider."""

from __future__ import annotations

import logging

from ....configuration.settings import VisionSettings
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..opencv_manager import OpenCVCameraManager
from ..provider import CameraProviderDefinition

logger = logging.getLogger(__name__)


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
    result = manager.start()
    if int(result["started"]) <= 0:
        manager.stop()
        raise DeviceInitializationError(
            "OpenCV camera provider could not start any configured camera"
        )
    logger.info(
        "OpenCV camera provider started: %d online, %d failed",
        result["started"],
        result["failed"],
    )
    return manager


OPENCV_CAMERA_PROVIDER = CameraProviderDefinition(
    name="opencv",
    capabilities=frozenset({DeviceCapability.CAMERA}),
    create=create_opencv_camera,
)

__all__ = ["OPENCV_CAMERA_PROVIDER", "create_opencv_camera"]
