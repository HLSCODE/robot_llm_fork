"""OpenCV 本地摄像头管理器 — 采集与流式推送。"""

import logging
import threading
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
        self._backend = backend if backend is not None else getattr(cv2, "CAP_DSHOW", 0)

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
        if not _CV_AVAILABLE:
            raise RuntimeError("opencv-python 未安装")

        self._captures.clear()
        self._failed_cameras.clear()

        for cam in self._cameras:
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
