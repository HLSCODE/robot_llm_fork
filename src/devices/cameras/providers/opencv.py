"""OpenCV camera provider."""

from __future__ import annotations

import logging

from ....configuration.settings import VisionSettings
from ...runtime.models import DeviceCapability
from ..opencv_manager import OpenCVCameraManager
from ..provider import CameraProviderDefinition

logger = logging.getLogger(__name__)


def create_opencv_camera(settings: VisionSettings) -> OpenCVCameraManager:
    indexes = [
        int(value.strip())
        for value in settings.webcam_device_indexes.split(",")
        if value.strip()
    ] or [0]
    names = [
        value.strip()
        for value in settings.webcam_device_names.split(",")
        if value.strip()
    ]
    cameras = [
        {
            "index": index,
            "name": names[position] if position < len(names) else f"webcam-{index}",
        }
        for position, index in enumerate(indexes)
    ]
    manager = OpenCVCameraManager(
        cameras=cameras,
        fps=settings.webcam_fps,
        width=settings.webcam_width,
        height=settings.webcam_height,
        jpeg_quality=settings.webcam_jpeg_quality,
        encode_fps=settings.camera_encode_fps,
    )
    result = manager.start()
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
