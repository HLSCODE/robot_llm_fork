"""Intel RealSense camera provider."""

from __future__ import annotations

from ....configuration.settings import VisionSettings
from ...runtime.models import DeviceCapability, DeviceInitializationError
from ..provider import CameraProviderDefinition
from ..realsense_manager import RealSenseManager, probe_realsense_cameras

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
    return manager


def probe_realsense_provider(
    settings: VisionSettings,
    timeout_seconds: float,
    max_attempts: int,
) -> tuple[dict[str, object], ...]:
    profiles = settings.camera_profiles_for_provider("realsense")
    fps = settings.realsense_fps or (30 if len(profiles) <= 2 else 15)
    return probe_realsense_cameras(
        tuple({"serial": profile.device_id, "name": profile.name} for profile in profiles),
        width=settings.realsense_color_width,
        height=settings.realsense_color_height,
        fps=fps,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )


REALSENSE_CAMERA_PROVIDER = CameraProviderDefinition(
    name="realsense",
    capabilities=frozenset({DeviceCapability.CAMERA}),
    create=create_realsense_camera,
    probe=probe_realsense_provider,
)

__all__ = ["REALSENSE_CAMERA_PROVIDER", "create_realsense_camera"]
