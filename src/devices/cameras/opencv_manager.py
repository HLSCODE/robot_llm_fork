"""OpenCV 本地摄像头管理器 — 采集与流式推送。"""

import logging
import platform
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)


class _ConfiguredCamera(TypedDict):
    index: int
    name: str


try:
    import cv2

    _CV_AVAILABLE = True
except ImportError:
    _CV_AVAILABLE = False
    logger.debug("opencv-python 未安装 — 相机流媒体不可用")


class OpenCVCameraManager:
    """Manage one or more local webcams through OpenCV."""

    def __init__(
        self,
        cameras: Sequence[Mapping[str, object]] = (),
        fps: int = 30,
        width: int = 640,
        height: int = 480,
        jpeg_quality: int = 85,
        encode_fps: int | None = None,
        backend: Optional[int] = None,
    ) -> None:
        self._cameras = [_normalize_camera(camera) for camera in cameras]
        self._fps = fps
        self._width = width
        self._height = height
        self._jpeg_quality = jpeg_quality
        self._encode_fps = max(1, encode_fps or fps)
        self._backend = backend if backend is not None else _default_backend()

        self._captures: list[tuple[int, str, "cv2.VideoCapture"]] = []
        self._failed_cameras: list[dict] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest_jpegs: list[tuple[str, str, bytes]] = []

    @property
    def is_available(self) -> bool:
        return _CV_AVAILABLE

    @property
    def camera_count(self) -> int:
        return len(self._captures)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> dict:
        """Compatibility alias that activates every configured camera."""
        return self.activate()

    def activate(self, camera_names: Sequence[str] = ()) -> dict:
        if not _CV_AVAILABLE:
            raise RuntimeError("opencv-python 未安装")

        selected_names = {name.strip() for name in camera_names if name.strip()}
        selected_cameras = [
            camera
            for camera in self._cameras
            if not selected_names
            or camera["name"] in selected_names
            or str(camera["index"]) in selected_names
            or f"webcam:{camera['index']}" in selected_names
        ]
        if selected_names and not selected_cameras:
            raise ValueError(f"unknown camera selection: {', '.join(sorted(selected_names))}")
        active_names = {name for _index, name, _capture in self._captures}
        desired_names = {camera["name"] for camera in selected_cameras}
        if self._running and active_names == desired_names:
            return {"started": len(self._captures), "failed": 0}
        if self._running or self._captures:
            self.stop()

        self._captures.clear()
        self._failed_cameras.clear()

        for cam in selected_cameras:
            index = cam["index"]
            name = cam["name"]
            capture = cv2.VideoCapture(index, self._backend)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            capture.set(cv2.CAP_PROP_FPS, self._fps)

            if not capture.isOpened():
                self._failed_cameras.append(
                    {"serial": f"webcam:{index}", "name": name, "error": "摄像头无法打开"}
                )
                capture.release()
                continue

            ok, _ = capture.read()
            if not ok:
                self._failed_cameras.append(
                    {"serial": f"webcam:{index}", "name": name, "error": "摄像头无法读取画面"}
                )
                capture.release()
                continue

            self._captures.append((index, name, capture))

        if self._captures:
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
        else:
            logger.warning("所有本地摄像头均无法启动 (%d 路失败)", len(self._failed_cameras))

        return {"started": len(self._captures), "failed": len(self._failed_cameras)}

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        for _, _, capture in self._captures:
            try:
                capture.release()
            except Exception:
                pass
        self._captures.clear()
        with self._lock:
            self._latest_jpegs.clear()

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpegs[0][2] if self._latest_jpegs else None

    def get_latest_jpegs(self) -> list[tuple[str, str, bytes]]:
        with self._lock:
            return list(self._latest_jpegs)

    def get_cameras_info(self) -> list[dict]:
        online = {index for index, _, _ in self._captures}
        failed_map = {c["serial"]: c["error"] for c in self._failed_cameras}
        result = []
        for cam in self._cameras:
            serial = f"webcam:{cam['index']}"
            item = {"serial": serial, "name": cam["name"], "online": cam["index"] in online}
            if cam["index"] not in online:
                item["error"] = failed_map.get(serial, "未启动")
            result.append(item)
        return result

    def _capture_loop(self) -> None:
        interval = 1.0 / max(self._encode_fps, 1)
        while self._running:
            frames: list[tuple[str, str, bytes]] = []
            for index, name, capture in self._captures:
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                ok, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
                )
                if ok:
                    frames.append((f"webcam:{index}", name, bytes(buf)))
            with self._lock:
                self._latest_jpegs = frames
            threading.Event().wait(interval)


def _normalize_camera(config: Mapping[str, object]) -> _ConfiguredCamera:
    raw_index = config.get("index", 0)
    if isinstance(raw_index, bool) or not isinstance(raw_index, (int, float, str)):
        raise TypeError("camera index must be an integer or numeric string")
    index = int(raw_index)
    raw_name = config.get("name", "")
    name = str(raw_name) if raw_name else f"webcam-{index}"
    return {"index": index, "name": name}


def probe_opencv_cameras(
    cameras: Sequence[Mapping[str, object]],
    *,
    width: int,
    height: int,
    fps: int,
    backend: int | None,
    timeout_seconds: float,
    max_attempts: int,
) -> tuple[dict[str, object], ...]:
    """Probe local cameras sequentially without encoding frames."""
    if not _CV_AVAILABLE:
        raise RuntimeError("opencv-python 未安装")
    selected_backend = backend if backend is not None else _default_backend()
    results: list[dict[str, object]] = []
    for raw_camera in cameras:
        camera = _normalize_camera(raw_camera)
        index = camera["index"]
        name = camera["name"]
        succeeded = False
        last_error = "摄像头无法打开"
        for attempt in range(max_attempts):
            capture = cv2.VideoCapture(index, selected_backend)
            try:
                read_timeout_property = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
                if read_timeout_property is not None:
                    capture.set(read_timeout_property, max(1, int(timeout_seconds * 1000)))
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                capture.set(cv2.CAP_PROP_FPS, fps)
                if not capture.isOpened():
                    last_error = "未连接"
                else:
                    deadline = time.monotonic() + timeout_seconds
                    while time.monotonic() < deadline:
                        ok, frame = capture.read()
                        if ok and frame is not None:
                            succeeded = True
                            break
                    if not succeeded:
                        last_error = f"{timeout_seconds:g} 秒内未获得有效帧"
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
            finally:
                capture.release()
            if succeeded:
                break
            if last_error == "未连接":
                break
            if attempt + 1 < max_attempts:
                time.sleep(0.2)
        results.append(
            {
                "serial": f"webcam:{index}",
                "name": name,
                "online": succeeded,
                "frame_received": succeeded,
                **({} if succeeded else {"error": last_error}),
            }
        )
    return tuple(results)


def _default_backend() -> int:
    if platform.system() == "Windows":
        return int(getattr(cv2, "CAP_DSHOW", 0))
    return int(getattr(cv2, "CAP_ANY", 0))
