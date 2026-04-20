"""相机管理器工厂 — 根据配置初始化并返回对应的相机单例。

对外只暴露一个入口:
    get_camera_manager() → RealSenseManager | OpenCVCameraManager | None

调用方无需关心具体相机类型，只需调用返回对象的公共接口
（get_latest_jpegs / get_latest_jpeg / get_cameras_info 等）。
"""

import logging
from typing import Union, Optional

from ..core.config_loader import Config
from .realsense_manager import RealSenseManager
from .opencv_manager import OpenCVCameraManager

logger = logging.getLogger(__name__)

CameraManager = Union[RealSenseManager, OpenCVCameraManager]


def get_camera_manager() -> Optional[CameraManager]:
    """根据 CAMERA_PROVIDER 初始化并返回相机管理器单例。

    CAMERA_PROVIDER=realsense（默认）→ RealSenseManager
    CAMERA_PROVIDER=webcam          → OpenCVCameraManager

    单例已运行时直接返回，不重复启动。
    任何异常均被捕获并记录，返回 None 以保证服务正常启动。
    """
    try:
        config = Config.get_instance()
        provider = getattr(config, "CAMERA_PROVIDER", "realsense").lower()

        if provider in ("webcam", "opencv"):
            return _get_opencv_manager(config)
        return _get_realsense_manager(config)

    except Exception as exc:
        logger.info("相机管理器未启动 (%s)", exc)
        return None


# ------------------------------------------------------------------
# 内部实现
# ------------------------------------------------------------------

def _get_realsense_manager(config) -> Optional[RealSenseManager]:
    """初始化 RealSense 相机管理器单例。

    从配置读取 REALSENSE_DEVICE_SN / REALSENSE_DEVICE_NAMES，
    未配置序列号时返回 None。
    """
    sn_str = getattr(config, "REALSENSE_DEVICE_SN", "")
    names_str = getattr(config, "REALSENSE_DEVICE_NAMES", "")
    serials = [s.strip() for s in sn_str.split(",") if s.strip()] if sn_str else []
    names = [n.strip() for n in names_str.split(",") if n.strip()] if names_str else []

    if not serials:
        logger.info("未配置相机序列号，跳过 RealSense 初始化")
        return None

    cameras = [
        {"serial": serial, "name": names[i] if i < len(names) else serial}
        for i, serial in enumerate(serials)
    ]
    mgr = RealSenseManager.get_instance(cameras=cameras, fps=30, width=640, height=480, jpeg_quality=85)
    if not mgr.is_running:
        result = mgr.start()
        logger.info("RealSense 相机管理器已启动: %d 路在线, %d 路失败", result["started"], result["failed"])
    return mgr


def _get_opencv_manager(config) -> OpenCVCameraManager:
    """初始化 OpenCV 本地摄像头管理器单例。

    从配置读取 WEBCAM_DEVICE_INDEXES / WEBCAM_DEVICE_NAMES。
    """
    indexes_str = getattr(config, "WEBCAM_DEVICE_INDEXES", "0")
    names_str = getattr(config, "WEBCAM_DEVICE_NAMES", "")
    indexes = [int(x.strip()) for x in indexes_str.split(",") if x.strip()] or [0]
    names = [n.strip() for n in names_str.split(",") if n.strip()] if names_str else []

    cameras = [
        {"index": index, "name": names[i] if i < len(names) else f"webcam-{index}"}
        for i, index in enumerate(indexes)
    ]
    mgr = OpenCVCameraManager.get_instance(cameras=cameras, fps=30, width=640, height=480, jpeg_quality=85)
    if not mgr.is_running:
        result = mgr.start()
        logger.info("OpenCV 相机管理器已启动: %d 路在线, %d 路失败", result["started"], result["failed"])
    return mgr
