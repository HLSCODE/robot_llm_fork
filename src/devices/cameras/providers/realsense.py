"""Intel RealSense camera provider."""

from __future__ import annotations

import logging

from ....configuration.settings import VisionSettings
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..provider import CameraProviderDefinition
from ..realsense_manager import RealSenseManager

logger = logging.getLogger(__name__)


def create_realsense_camera(settings: VisionSettings) -> RealSenseManager:
    serials = [
        value.strip()
        for value in settings.realsense_device_sn.split(",")
        if value.strip()
    ]
    if not serials:
        raise DeviceInitializationError(
            "RealSense provider requires REALSENSE_DEVICE_SN"
        )
    names = [
        value.strip()
        for value in settings.realsense_device_names.split(",")
        if value.strip()
    ]
    cameras = [
        {
            "serial": serial,
            "name": names[position] if position < len(names) else serial,
        }
        for position, serial in enumerate(serials)
    ]
    automatic_fps = 30 if len(cameras) <= 2 else 15
    fps = settings.realsense_fps or automatic_fps
    manager = RealSenseManager(
        cameras=cameras,
        fps=fps,
        width=settings.realsense_color_width,
        height=settings.realsense_color_height,
        depth_width=settings.realsense_depth_width,
        depth_height=settings.realsense_depth_height,
        depth_fps=fps,
        jpeg_quality=settings.realsense_jpeg_quality,
        align_depth_to_color=settings.realsense_align_depth_to_color,
        encode_fps=settings.camera_encode_fps,
    )
    result = manager.start()
    logger.info(
        "RealSense camera provider started: %d online, %d failed",
        result["started"],
        result["failed"],
    )
    return manager


REALSENSE_CAMERA_PROVIDER = CameraProviderDefinition(
    name="realsense",
    capabilities=frozenset({DeviceCapability.CAMERA}),
    create=create_realsense_camera,
)

__all__ = ["REALSENSE_CAMERA_PROVIDER", "create_realsense_camera"]
