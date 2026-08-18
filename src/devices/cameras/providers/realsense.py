"""Intel RealSense camera provider."""

from __future__ import annotations

import logging

from ....configuration.settings import VisionSettings
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..provider import CameraProviderDefinition
from ..realsense_manager import RealSenseManager

logger = logging.getLogger(__name__)


def create_realsense_camera(settings: VisionSettings) -> RealSenseManager:
    profiles = settings.camera_profiles_for_provider("realsense")
    if not profiles:
        raise DeviceInitializationError(
            "RealSense provider requires at least one [[vision.cameras]] profile"
        )
    cameras = [
        {
            "serial": profile.device_id,
            "name": profile.name,
        }
        for profile in profiles
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
    if int(result["started"]) <= 0:
        manager.stop()
        raise DeviceInitializationError(
            "RealSense camera provider could not start any configured camera"
        )
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
