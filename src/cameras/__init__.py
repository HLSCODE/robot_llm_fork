"""相机管理器模块。

包含:
    RealSenseManager    — Intel RealSense 深度相机（多路）
    OpenCVCameraManager — 本地 USB/内置摄像头（OpenCV）
    get_camera_manager  — 根据配置返回对应的相机管理器单例
"""

from .realsense_manager import RealSenseManager
from .opencv_manager import OpenCVCameraManager
from .camera_factory import get_camera_manager

__all__ = ["RealSenseManager", "OpenCVCameraManager", "get_camera_manager"]
