from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import time

from ..devices import (
    BalanceReader,
    CameraSource,
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
)
from ..devices.runtime.ids import BALANCE
from ..devices.sensors.balance import SimulatedBalanceReader, VisionBalanceReader
from ..configuration.settings import CameraRole, VisionSettings
from ..llm import BALANCE_READING_PROFILE, LLMContentPart, LLMRegistry
from .camera_access import CameraAccessService


class ManagedBalanceCameraCapture:
    """Capture one balance image through the shared camera lease."""

    def __init__(
        self,
        camera_access: CameraAccessService,
        *,
        camera_name: str,
        wait_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if wait_timeout_seconds <= 0:
            raise ValueError("balance camera wait timeout must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("balance camera poll interval must be positive")
        normalized_camera_name = camera_name.strip()
        if not normalized_camera_name:
            raise ValueError("balance camera name must not be empty")
        self._camera_access = camera_access
        self._camera_name = normalized_camera_name
        self._wait_timeout_seconds = wait_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleep = sleep

    def __call__(self) -> bytes:
        with self._camera_access.open("balance-reading") as camera:
            return self._wait_for_frame(camera)

    def _wait_for_frame(self, camera: CameraSource) -> bytes:
        deadline = self._clock() + self._wait_timeout_seconds
        while True:
            for serial, name, jpeg in camera.get_latest_jpegs():
                if not jpeg:
                    continue
                if self._camera_name not in (serial, name):
                    continue
                return bytes(jpeg)
            if self._clock() >= deadline:
                raise TimeoutError(f"等待电子秤相机画面超时: {self._camera_name}")
            self._sleep(self._poll_interval_seconds)


class LLMBalanceDisplayRecognizer:
    """Recognize a balance display through the unified LLM task registry."""

    def __init__(self, llm: LLMRegistry) -> None:
        self._llm = llm

    def __call__(self, jpeg: bytes) -> str:
        if not jpeg:
            raise ValueError("balance image must not be empty")
        result = asyncio.run(
            self._llm.vision_fusion.observe(
                images=(
                    LLMContentPart(
                        type="image",
                        data=base64.b64encode(jpeg).decode("ascii"),
                        mime_type="image/jpeg",
                    ),
                ),
                question="读取电子秤显示屏当前数值。",
                profile=BALANCE_READING_PROFILE,
            )
        )
        return result.text


def register_balance_reader(
    runtime: DeviceRuntime,
    camera_access: CameraAccessService,
    llm: LLMRegistry,
    settings: VisionSettings,
    *,
    simulation: bool,
) -> None:
    if simulation:
        def factory() -> BalanceReader:
            return SimulatedBalanceReader()
    else:
        recognize = LLMBalanceDisplayRecognizer(llm)

        def factory() -> BalanceReader:
            camera_profile = settings.require_camera_for_role(CameraRole.BALANCE)
            capture = ManagedBalanceCameraCapture(
                camera_access,
                camera_name=camera_profile.name,
                wait_timeout_seconds=settings.balance_camera_wait_timeout_seconds,
            )
            return VisionBalanceReader(capture, recognize)

    runtime.register(
        DeviceRegistration(
            device_id=BALANCE,
            capabilities=frozenset({DeviceCapability.BALANCE_READER}),
            factory=factory,
            close=lambda reader: reader.close(),
        )
    )
