"""相机管理器工厂 — 根据配置创建由 DeviceRuntime 持有的相机。

对外只暴露一个入口:
    create_camera_manager() → RealSenseManager | OpenCVCameraManager | None

调用方无需关心具体相机类型，只需调用返回对象的公共接口
（get_latest_jpegs / get_latest_jpeg / get_cameras_info 等）。
"""

import logging
from typing import Union, Optional

from ...configuration.settings import VisionSettings
from .realsense_manager import RealSenseManager
from .opencv_manager import OpenCVCameraManager

logger = logging.getLogger(__name__)

CameraManager = Union[RealSenseManager, OpenCVCameraManager]


def create_camera_manager(
    settings: VisionSettings,
) -> Optional[CameraManager]:
    """根据 CAMERA_PROVIDER 创建并启动相机管理器。

    CAMERA_PROVIDER=realsense（默认）→ RealSenseManager
    CAMERA_PROVIDER=webcam          → OpenCVCameraManager

    任何异常均被捕获并记录，返回 None 以保证服务正常启动。
    """
    try:
        provider = settings.camera_provider.lower()

        if provider in ("webcam", "opencv"):
            return _get_opencv_manager(settings)
        return _get_realsense_manager(settings)

    except Exception as exc:
        logger.info("相机管理器未启动 (%s)", exc)
        return None


# ------------------------------------------------------------------
# 内部实现
# ------------------------------------------------------------------

def _get_realsense_manager(
    settings: VisionSettings,
) -> Optional[RealSenseManager]:
    """初始化 RealSense 相机管理器。

    从配置读取 REALSENSE_DEVICE_SN / REALSENSE_DEVICE_NAMES，
    未配置序列号时返回 None。
    """
    sn_str = settings.realsense_device_sn
    names_str = settings.realsense_device_names
    serials = [s.strip() for s in sn_str.split(",") if s.strip()] if sn_str else []
    names = [n.strip() for n in names_str.split(",") if n.strip()] if names_str else []

    if not serials:
        logger.info("未配置相机序列号，跳过 RealSense 初始化")
        return None

    cameras = [
        {"serial": serial, "name": names[i] if i < len(names) else serial}
        for i, serial in enumerate(serials)
    ]
    # 多相机时降低 FPS 以避免 USB 带宽过载（3+ 路 → 15fps）
    auto_fps = 30 if len(cameras) <= 2 else 15
    fps = settings.realsense_fps or auto_fps
    color_width = settings.realsense_color_width
    color_height = settings.realsense_color_height
    depth_width = settings.realsense_depth_width
    depth_height = settings.realsense_depth_height
    jpeg_quality = settings.realsense_jpeg_quality
    encode_fps = settings.camera_encode_fps
    align_depth = settings.realsense_align_depth_to_color
    logger.info(
        "RealSense 相机数=%d, color=%dx%d, depth=%dx%d, FPS=%d, encode_fps=%d",
        len(cameras),
        color_width,
        color_height,
        depth_width,
        depth_height,
        fps,
        encode_fps,
    )
    mgr = RealSenseManager(
        cameras=cameras,
        fps=fps,
        width=color_width,
        height=color_height,
        depth_width=depth_width,
        depth_height=depth_height,
        depth_fps=fps,
        jpeg_quality=jpeg_quality,
        align_depth_to_color=align_depth,
        encode_fps=encode_fps,
    )
    if not mgr.is_running:
        result = mgr.start()
        logger.info("RealSense 相机管理器已启动: %d 路在线, %d 路失败", result["started"], result["failed"])
    return mgr


def _get_opencv_manager(settings: VisionSettings) -> OpenCVCameraManager:
    """初始化 OpenCV 本地摄像头管理器。

    从配置读取 WEBCAM_DEVICE_INDEXES / WEBCAM_DEVICE_NAMES。
    """
    indexes_str = settings.webcam_device_indexes
    names_str = settings.webcam_device_names
    indexes = [int(x.strip()) for x in indexes_str.split(",") if x.strip()] or [0]
    names = [n.strip() for n in names_str.split(",") if n.strip()] if names_str else []

    cameras = [
        {"index": index, "name": names[i] if i < len(names) else f"webcam-{index}"}
        for i, index in enumerate(indexes)
    ]
    mgr = OpenCVCameraManager(
        cameras=cameras,
        fps=settings.webcam_fps,
        width=settings.webcam_width,
        height=settings.webcam_height,
        jpeg_quality=settings.webcam_jpeg_quality,
        encode_fps=settings.camera_encode_fps,
    )
    if not mgr.is_running:
        result = mgr.start()
        logger.info("OpenCV 相机管理器已启动: %d 路在线, %d 路失败", result["started"], result["failed"])
    return mgr
