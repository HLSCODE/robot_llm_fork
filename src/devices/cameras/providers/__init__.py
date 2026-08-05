"""Built-in camera providers."""

from .opencv import OPENCV_CAMERA_PROVIDER
from .realsense import REALSENSE_CAMERA_PROVIDER

__all__ = ["OPENCV_CAMERA_PROVIDER", "REALSENSE_CAMERA_PROVIDER"]
