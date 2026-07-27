"""相机管理器模块。

包含:
    RealSenseManager    — Intel RealSense 深度相机（多路）
    OpenCVCameraManager — 本地 USB/内置摄像头（OpenCV）
    设备实例由 DeviceRuntime 创建和管理。
"""

from .realsense_manager import RealSenseManager
from .opencv_manager import OpenCVCameraManager

__all__ = ["RealSenseManager", "OpenCVCameraManager"]
