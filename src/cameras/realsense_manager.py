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
import traceback
from typing import Optional

logger = logging.getLogger(__name__)

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
        depth_width: int | None = None,
        depth_height: int | None = None,
        depth_fps: int | None = None,
        jpeg_quality: int = 85,
        grid_cols: int = 2,
        output_width: int = 0,
        output_height: int = 0,
        align_depth_to_color: bool = True,
        encode_fps: int | None = None,
    ) -> None:
        # 规范化相机配置，缺省 name 用 serial 代替
        self._cameras: list[dict] = [
            {"serial": c.get("serial", ""), "name": c.get("name", "") or c.get("serial", "")}
            for c in cameras
        ]
        self._fps = fps
        self._width = width
        self._height = height
        self._depth_width = depth_width or width
        self._depth_height = depth_height or height
        self._depth_fps = depth_fps or fps
        self._jpeg_quality = jpeg_quality
        self._encode_fps = max(1, encode_fps or fps)
        self._grid_cols = max(1, grid_cols)
        self._output_width = output_width
        self._output_height = output_height
        self._align_depth_to_color = align_depth_to_color

        # (serial, name, pipeline)
        self._pipelines: list[tuple[str, str, "rs.pipeline"]] = []
        # 启动失败的相机: {"serial": ..., "name": ..., "error": ...}
        self._failed_cameras: list[dict] = []

        self._running = False
        # 每路相机独立采集线程 + 独立编码线程
        self._cam_threads: list[threading.Thread] = []
        self._encode_thread: Optional[threading.Thread] = None
        # 各相机最新原始帧: serial -> (name, color_bgr, depth_uint16, intrinsics)
        # intrinsics 格式: {"fx": float, "fy": float, "ppx": float, "ppy": float}
        self._raw_frames: dict[str, tuple[str, "np.ndarray", "np.ndarray", dict]] = {}
        self._intrinsics_cache: dict[str, dict] = {}  # serial -> intrinsics dict
        self._raw_lock = threading.Lock()
        # 编码结果，由编码线程写入，外部只读
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        # (serial, name, jpeg_bytes)
        self._latest_jpegs: list[tuple[str, str, bytes]] = []

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

        import time as _time

        # 预扫描所有设备的传感器信息（用于精准判断 IMU 是否存在）
        device_sensors: dict[str, list[str]] = {}
        try:
            for d in ctx.query_devices():
                sn = d.get_info(rs.camera_info.serial_number)
                try:
                    device_sensors[sn] = [
                        s.get_info(rs.camera_info.name) for s in d.query_sensors()
                    ]
                except Exception:
                    device_sensors[sn] = []
        except Exception:
            pass

        for cam in self._cameras:
            serial: str = cam["serial"]
            name: str = cam["name"]

            # 检查设备是否已连接（serial 非空时才做预检）
            if serial and serial not in connected_serials:
                msg = f"设备未找到（已连接: {sorted(connected_serials) or '无'}）"
                logger.warning("相机 %s (%s): %s", name, serial, msg)
                self._failed_cameras.append({"serial": serial, "name": name, "error": msg})
                continue

            # 打印设备诊断信息（产品名、固件版本、传感器列表）
            try:
                for d in ctx.query_devices():
                    if d.get_info(rs.camera_info.serial_number) == serial:
                        product = d.get_info(rs.camera_info.name)
                        fw = d.get_info(rs.camera_info.firmware_version)
                        usb = d.get_info(rs.camera_info.usb_type_descriptor)
                        sensors = device_sensors.get(serial, [])
                        logger.info("相机 %s (%s): 产品=%s 固件=%s USB=%s 传感器=%s",
                                    name, serial, product, fw, usb, sensors)
                        break
            except Exception:
                pass

            # 构建 pipeline 配置
            def _build_config():
                _pipeline = rs.pipeline(ctx)
                _cfg = rs.config()
                # 先清空所有默认流（D435if 默认会带 IMU），再只启用需要的
                try:
                    _cfg.disable_all_streams()
                except Exception:
                    pass
                if serial:
                    _cfg.enable_device(serial)
                _cfg.enable_stream(rs.stream.color, self._width, self._height,
                                   rs.format.bgr8, self._fps)
                _cfg.enable_stream(rs.stream.depth, self._depth_width, self._depth_height,
                                   rs.format.z16, self._depth_fps)
                return _pipeline, _cfg

            # 带重试的启动（USB 带宽协商偶尔需要多次尝试）
            started = False
            last_error = ""
            for attempt in range(3):
                pipeline, cfg = _build_config()
                try:
                    pipeline.start(cfg)
                    self._pipelines.append((serial, name, pipeline))
                    logger.info("RealSense 相机已启动: name=%s serial=%s (attempt %d)",
                                name, serial, attempt + 1)
                    started = True
                    break
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("相机 %s (%s) 启动失败 (attempt %d/%d): %s",
                                   name, serial, attempt + 1, 3, last_error)
                    # 清理失败的 pipeline
                    try:
                        pipeline.stop()
                    except Exception:
                        pass
                    if attempt < 2:
                        _time.sleep(1.0)  # 等 USB 带宽释放再重试

            if not started:
                logger.warning("相机 %s (%s) 三次尝试均失败: %s\n%s",
                               name, serial, last_error, traceback.format_exc())
                self._failed_cameras.append({"serial": serial, "name": name, "error": last_error})

            # 相机之间错开启动，避免 USB 带宽协商碰撞
            _time.sleep(0.5)

        if self._pipelines:
            self._running = True
            self._raw_frames.clear()
            self._intrinsics_cache.clear()
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
        with self._raw_lock:
            self._raw_frames.clear()
        with self._lock:
            self._latest_jpeg = None
            self._latest_jpegs.clear()

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
        """每路相机独立线程：持续采集彩色+深度帧及内参，写入 _raw_frames。"""
        # 给相机自动曝光/白平衡留出初始化时间
        import time as _time
        _time.sleep(1.0)

        align = rs.align(rs.stream.color) if self._align_depth_to_color else None
        fail_count = 0
        while self._running:
            try:
                frameset = pipeline.wait_for_frames(timeout_ms=1000)
                if align is not None:
                    frameset = align.process(frameset)
                color_frame = frameset.get_color_frame()
                depth_frame = frameset.get_depth_frame()
                if color_frame and depth_frame:
                    if fail_count > 0:
                        logger.info("相机 %s (%s) 帧恢复，之前连续失败 %d 次", name, serial, fail_count)
                    fail_count = 0
                    color_arr = np.asanyarray(color_frame.get_data())
                    depth_arr = np.asanyarray(depth_frame.get_data())
                    # 内参首次获取后缓存（同一相机内参不变）
                    if serial not in self._intrinsics_cache:
                        profile = color_frame.get_profile()
                        intr = profile.as_video_stream_profile().get_intrinsics()
                        self._intrinsics_cache[serial] = {
                            "fx": intr.fx, "fy": intr.fy,
                            "ppx": intr.ppx, "ppy": intr.ppy,
                        }
                        logger.info("相机 %s (%s) 内参已缓存: fx=%.1f fy=%.1f", name, serial, intr.fx, intr.fy)
                    with self._raw_lock:
                        self._raw_frames[serial] = (name, color_arr, depth_arr, self._intrinsics_cache[serial])
                else:
                    fail_count += 1
                    if fail_count <= 3:
                        logger.warning("相机 %s (%s) 帧不完整: color=%s depth=%s",
                                       name, serial,
                                       color_frame is not None, depth_frame is not None)
            except Exception as exc:
                fail_count += 1
                if fail_count <= 3:
                    logger.warning("相机 %s (%s) 取帧异常: %s", name, serial, exc)
                elif fail_count == 4:
                    logger.warning("相机 %s (%s) 连续取帧失败已达 %d 次，后续静默", name, serial, fail_count)

    def get_latest_raw_frames(self, camera_name: str | None = None) -> tuple | None:
        """获取最新原始帧（线程安全）。

        Args:
            camera_name: 指定相机名称或序列号，为 None 时返回第一路在线相机的帧。

        Returns:
            (color_bgr: np.ndarray, depth_uint16: np.ndarray, intrinsics: dict) 或 None
        """
        with self._raw_lock:
            if not self._raw_frames:
                return None
            if camera_name is not None:
                for serial, (name, color, depth, intr) in self._raw_frames.items():
                    if name == camera_name or serial == camera_name:
                        return (color.copy(), depth.copy(), dict(intr))
                return None
            # 返回第一路
            _, (_, color, depth, intr) = next(iter(self._raw_frames.items()))
            return (color.copy(), depth.copy(), dict(intr))

    def _encode_loop(self) -> None:
        """编码线程：读取所有相机最新原始帧，编码为 JPEG 写入公开缓冲区。"""
        interval = 1.0 / max(self._encode_fps, 1)
        while self._running:
            with self._raw_lock:
                snapshot = [(serial, name, color) for serial, (name, color, _depth, _intr) in self._raw_frames.items()]
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
