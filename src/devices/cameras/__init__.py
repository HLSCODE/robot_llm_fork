"""Camera capability implementations and provider registry."""

from .opencv_manager import OpenCVCameraManager
from .realsense_manager import RealSenseManager
from .registry import CAMERA_PROVIDERS, resolve_camera_provider

__all__ = [
    "CAMERA_PROVIDERS",
    "OpenCVCameraManager",
    "RealSenseManager",
    "resolve_camera_provider",
]
