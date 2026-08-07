"""
Camera adapters for voice interaction tasks.
"""
from __future__ import annotations

import base64
from contextlib import AbstractContextManager
import logging
import time
from typing import Any, Callable, Optional, Protocol, Sequence

from ...llm import LLMContentPart

logger = logging.getLogger(__name__)


class CameraProvider(Protocol):
    def capture_llm_parts(self) -> Sequence[LLMContentPart]:
        """Capture current camera frames as LLM content parts."""


class CameraCaptureError(RuntimeError):
    """Camera error with a user-facing message and hidden technical detail."""

    def __init__(self, user_message: str, technical_detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.technical_detail = technical_detail


class CamerasModuleProvider:
    """Capture LLM vision images through an injected runtime-owned camera."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[object]],
        *,
        camera_name: Optional[str] = None,
        wait_timeout_s: float = 2.0,
        poll_interval_s: float = 0.1,
        max_frames: Optional[int] = None,
    ) -> None:
        self.camera_name = (camera_name or "").strip()
        self.wait_timeout_s = max(0.0, float(wait_timeout_s))
        self.poll_interval_s = max(0.01, float(poll_interval_s))
        self.max_frames = max_frames if max_frames and max_frames > 0 else None
        self._session_factory = session_factory

    def capture_llm_parts(self) -> Sequence[LLMContentPart]:
        try:
            with self._session_factory() as manager:
                return self._capture_from_manager(manager)
        except CameraCaptureError:
            raise
        except Exception as exc:
            raise CameraCaptureError(
                "摄像头暂时不可用，无法读取画面。",
                f"相机会话或抓帧失败: {exc}",
            ) from exc

    def _capture_from_manager(
        self,
        manager: object,
    ) -> Sequence[LLMContentPart]:
        if manager is None:
            raise CameraCaptureError(
                "我现在还没接上摄像头，暂时看不到周围环境。",
                "相机会话没有返回相机能力",
            )
        frames = self._wait_for_jpegs(manager)
        if not frames:
            info = self._format_cameras_info(manager)
            if self.camera_name:
                user_message = f"我现在还没看到 {self.camera_name} 的画面，可能是相机还没连好。"
                technical = f"未获取到有效摄像头画面({self.camera_name})，相机状态: {info}"
            else:
                user_message = "我现在还没看到摄像头画面，可能是相机还没连好。"
                technical = f"未获取到有效摄像头画面，相机状态: {info}"
            raise CameraCaptureError(user_message, technical)

        if self.max_frames is not None:
            frames = frames[: self.max_frames]

        parts = []
        for serial, name, jpeg_bytes in frames:
            parts.append(
                LLMContentPart(
                    type="image",
                    data=base64.b64encode(jpeg_bytes).decode("ascii"),
                    mime_type="image/jpeg",
                    metadata={"serial": serial, "name": name},
                )
            )
        return parts

    def _wait_for_jpegs(self, manager: Any) -> list[tuple[str, str, bytes]]:
        deadline = time.monotonic() + self.wait_timeout_s
        while True:
            frames = self._read_jpegs(manager)
            if frames:
                return frames
            if time.monotonic() >= deadline:
                return []
            time.sleep(self.poll_interval_s)

    def _read_jpegs(self, manager: Any) -> list[tuple[str, str, bytes]]:
        frames: list[tuple[str, str, bytes]] = []
        if hasattr(manager, "get_latest_jpegs"):
            frames = [
                (str(serial), str(name), bytes(jpeg))
                for serial, name, jpeg in manager.get_latest_jpegs()
                if jpeg
            ]

        if self.camera_name:
            frames = [
                item for item in frames
                if item[0] == self.camera_name or item[1] == self.camera_name
            ]

        if not frames and not self.camera_name and hasattr(manager, "get_latest_jpeg"):
            jpeg = manager.get_latest_jpeg()
            if jpeg:
                frames = [("stitched", "stitched", bytes(jpeg))]

        return frames

    @staticmethod
    def _format_cameras_info(manager: Any) -> str:
        if not hasattr(manager, "get_cameras_info"):
            return "未知"
        try:
            info = manager.get_cameras_info()
        except Exception as exc:
            logger.debug("读取相机状态失败: %s", exc, exc_info=True)
            return "读取失败"
        if not info:
            return "无已配置相机"
        return "; ".join(
            f"{item.get('name') or item.get('serial')}:"
            f"{'online' if item.get('online') else item.get('error', 'offline')}"
            for item in info
        )
