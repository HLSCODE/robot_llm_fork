"""RealSense 多相机管理器 — RGB 帧采集与流式推送。

提供两种 WebSocket 流式推送模式:
    /camera/stream   — 拼接 JPEG 帧（二进制）
    /camera/frames   — 每路相机独立 JPEG 帧（JSON + base64）

相机由配置决定，每台相机需指定序列号（serial）和名称（name）。
不进行自动发现。若某台相机无法打开，仍继续启动其他相机，
并通过 get_cameras_info() 报告各相机状态。
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_instance: Optional["RealSenseManager"] = None
_instance_lock = threading.Lock()

try:
    import numpy as np
    import pyrealsense2 as rs
    _RS_AVAILABLE = True
except ImportError:
    _RS_AVAILABLE = False
    logger.debug("pyrealsense2 未安装 — 相机流媒体不可用")

try:
    import cv2
    _CV_AVAILABLE = True
except ImportError:
    _CV_AVAILABLE = False
    logger.debug("opencv-python 未安装 — 相机流媒体不可用")


class RealSenseManager:
    """管理多路 RealSense 相机管道，暴露最新拼接帧。

    在应用启动时调用 start()，退出时调用 stop()。
    WebSocket 处理器通过 get_latest_jpeg() / get_latest_jpegs() 获取最新帧。

    Args:
        cameras: 相机配置列表，每项包含 {"serial": "...", "name": "..."}。
                 若 serial 为空，RealSense SDK 将自动选择第一台设备。
    """

    def __init__(
        self,
        cameras: list[dict] = (),
        fps: int = 30,
        width: int = 640,
        height: int = 480,
        jpeg_quality: int = 85,
        grid_cols: int = 2,
        output_width: int = 0,
        output_height: int = 0,
    ) -> None:
        # 规范化相机配置，缺省 name 用 serial 代替
        self._cameras: list[dict] = [
            {"serial": c.get("serial", ""), "name": c.get("name", "") or c.get("serial", "")}
            for c in cameras
        ]
        self._fps = fps
        self._width = width
        self._height = height
        self._jpeg_quality = jpeg_quality
        self._grid_cols = max(1, grid_cols)
        self._output_width = output_width
        self._output_height = output_height

        # (serial, name, pipeline)
        self._pipelines: list[tuple[str, str, "rs.pipeline"]] = []
        # 启动失败的相机: {"serial": ..., "name": ..., "error": ...}
        self._failed_cameras: list[dict] = []

        self._running = False
        # 每路相机独立采集线程 + 独立编码线程
        self._cam_threads: list[threading.Thread] = []
        self._encode_thread: Optional[threading.Thread] = None
        # 各相机最新原始帧: serial -> (name, ndarray)，由采集线程写入
        self._raw_frames: dict[str, tuple[str, "np.ndarray"]] = {}
        self._raw_lock = threading.Lock()
        # 编码结果，由编码线程写入，外部只读
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        # (serial, name, jpeg_bytes)
        self._latest_jpegs: list[tuple[str, str, bytes]] = []

    @classmethod
    def get_instance(cls, **kwargs) -> "RealSenseManager":
        """返回全局单例。首次调用时以 kwargs 初始化，后续调用忽略参数直接返回已有实例。"""
        global _instance
        if _instance is None:
            with _instance_lock:
                if _instance is None:
                    _instance = cls(**kwargs)
        return _instance

    @classmethod
    def reset_instance(cls) -> None:
        """销毁单例（测试或重新配置时使用）。调用前请先手动 stop()。"""
        global _instance
        with _instance_lock:
            _instance = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return _RS_AVAILABLE and _CV_AVAILABLE

    @property
    def camera_count(self) -> int:
        """在线（已成功启动）相机数量。"""
        return len(self._pipelines)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> dict:
        """开启相机并启动后台采集线程。

        Returns:
            {"started": int, "failed": int}
            即使所有相机均失败也不抛出异常，失败信息可通过 get_cameras_info() 获取。

        Raises:
            RuntimeError: 仅在依赖库未安装时抛出。
        """
        if not _RS_AVAILABLE:
            raise RuntimeError("pyrealsense2 未安装")
        if not _CV_AVAILABLE:
            raise RuntimeError("opencv-python 未安装")

        self._pipelines.clear()
        self._failed_cameras.clear()

        # 获取当前已连接设备的序列号集合，用于快速判断设备是否存在
        try:
            ctx = rs.context()
            connected_serials = {
                d.get_info(rs.camera_info.serial_number)
                for d in ctx.query_devices()
            }
        except Exception:
            connected_serials = set()

        for cam in self._cameras:
            serial: str = cam["serial"]
            name: str = cam["name"]

            # 检查设备是否已连接（serial 非空时才做预检）
            if serial and serial not in connected_serials:
                msg = f"设备未找到（已连接: {sorted(connected_serials) or '无'}）"
                logger.warning("相机 %s (%s): %s", name, serial, msg)
                self._failed_cameras.append({"serial": serial, "name": name, "error": msg})
                continue

            pipeline = rs.pipeline()
            cfg = rs.config()
            if serial:
                cfg.enable_device(serial)
            cfg.enable_stream(
                rs.stream.color,
                self._width, self._height,
                rs.format.bgr8,
                self._fps,
            )
            try:
                pipeline.start(cfg)
                self._pipelines.append((serial, name, pipeline))
                logger.info("RealSense 相机已启动: name=%s serial=%s", name, serial)
            except Exception as exc:
                msg = str(exc)
                logger.warning("无法开启相机 %s (%s): %s", name, serial, msg)
                self._failed_cameras.append({"serial": serial, "name": name, "error": msg})

        if self._pipelines:
            self._running = True
            self._raw_frames.clear()
            for serial, name, pipeline in self._pipelines:
                t = threading.Thread(
                    target=self._camera_capture_loop,
                    args=(serial, name, pipeline),
                    daemon=True,
                    name=f"rs-capture-{name}",
                )
                t.start()
                self._cam_threads.append(t)
            self._encode_thread = threading.Thread(
                target=self._encode_loop, daemon=True, name="rs-encode"
            )
            self._encode_thread.start()
            logger.info(
                "相机采集线程已启动: %d 路在线, %d 路失败",
                len(self._pipelines), len(self._failed_cameras),
            )
        else:
            logger.warning(
                "所有配置相机均无法启动 (%d 路失败)", len(self._failed_cameras)
            )

        return {"started": len(self._pipelines), "failed": len(self._failed_cameras)}

    def stop(self) -> None:
        self._running = False
        for t in self._cam_threads:
            t.join(timeout=3.0)
        if self._encode_thread:
            self._encode_thread.join(timeout=3.0)
        self._cam_threads.clear()
        self._encode_thread = None
        for serial, name, pipeline in self._pipelines:
            try:
                pipeline.stop()
                logger.info("RealSense 相机已停止: name=%s serial=%s", name, serial)
            except Exception:
                pass
        self._pipelines.clear()

    def get_latest_jpeg(self) -> Optional[bytes]:
        """返回最新拼接 JPEG 帧（线程安全）。"""
        with self._lock:
            return self._latest_jpeg

    def get_latest_jpegs(self) -> list[tuple[str, str, bytes]]:
        """返回每路在线相机最新 (serial, name, jpeg_bytes) 列表（线程安全）。"""
        with self._lock:
            return list(self._latest_jpegs)

    def get_cameras_info(self) -> list[dict]:
        """返回所有已配置相机的状态列表（保持配置顺序）。

        每项格式:
            {"serial": str, "name": str, "online": bool}  — 在线
            {"serial": str, "name": str, "online": False, "error": str}  — 失败
        """
        online_serials = {serial for serial, _, _ in self._pipelines}
        failed_map = {c["serial"]: c["error"] for c in self._failed_cameras}
        result = []
        for cam in self._cameras:
            serial = cam["serial"]
            if serial in online_serials:
                result.append({"serial": serial, "name": cam["name"], "online": True})
            else:
                result.append({
                    "serial": serial,
                    "name": cam["name"],
                    "online": False,
                    "error": failed_map.get(serial, "未启动"),
                })
        return result

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _camera_capture_loop(self, serial: str, name: str, pipeline: "rs.pipeline") -> None:
        """每路相机独立线程：持续采集原始帧，写入 _raw_frames。"""
        while self._running:
            try:
                frameset = pipeline.wait_for_frames(timeout_ms=200)
                color = frameset.get_color_frame()
                if color:
                    arr = np.asanyarray(color.get_data())
                    with self._raw_lock:
                        self._raw_frames[serial] = (name, arr)
            except Exception as exc:
                logger.debug("帧超时 %s (%s): %s", name, serial, exc)

    def _encode_loop(self) -> None:
        """编码线程：读取所有相机最新原始帧，编码为 JPEG 写入公开缓冲区。"""
        interval = 1.0 / max(self._fps, 1)
        while self._running:
            with self._raw_lock:
                snapshot = [(serial, name, arr) for serial, (name, arr) in self._raw_frames.items()]
            if snapshot:
                jpeg = self._encode_stitched(snapshot)
                individual = self._encode_individual(snapshot)
                with self._lock:
                    if jpeg is not None:
                        self._latest_jpeg = jpeg
                    self._latest_jpegs = individual
            threading.Event().wait(interval)

    def _encode_individual(
        self, raw_frames: list[tuple[str, str, "np.ndarray"]]
    ) -> list[tuple[str, str, bytes]]:
        result = []
        for serial, name, img in raw_frames:
            ok, buf = cv2.imencode(
                ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
            )
            if ok:
                result.append((serial, name, bytes(buf)))
        return result

    def _encode_stitched(
        self, raw_frames: list[tuple[str, str, "np.ndarray"]]
    ) -> Optional[bytes]:
        if not raw_frames:
            return None
        imgs = [img for _, _, img in raw_frames]

        target_h, target_w = imgs[0].shape[:2]
        normed = []
        for f in imgs:
            if f.shape[:2] != (target_h, target_w):
                f = cv2.resize(f, (target_w, target_h))
            normed.append(f)

        COLS = self._grid_cols
        remainder = len(normed) % COLS
        if remainder:
            blank = np.zeros_like(normed[0])
            normed += [blank] * (COLS - remainder)

        rows = [np.hstack(normed[i:i + COLS]) for i in range(0, len(normed), COLS)]
        stitched = np.vstack(rows) if len(rows) > 1 else rows[0]

        if self._output_width > 0 and self._output_height > 0:
            stitched = cv2.resize(stitched, (self._output_width, self._output_height))
        elif self._output_width > 0:
            scale = self._output_width / stitched.shape[1]
            stitched = cv2.resize(
                stitched, (self._output_width, int(stitched.shape[0] * scale))
            )
        elif self._output_height > 0:
            scale = self._output_height / stitched.shape[0]
            stitched = cv2.resize(
                stitched, (int(stitched.shape[1] * scale), self._output_height)
            )

        ok, buf = cv2.imencode(
            ".jpg", stitched, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
        )
        return bytes(buf) if ok else None
