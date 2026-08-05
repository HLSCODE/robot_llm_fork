from __future__ import annotations

from collections.abc import Callable
import re
import time

from ...runtime.sensor_models import BalanceReading


_WEIGHT_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")


class VisionBalanceReader:
    """Balance provider composed from image capture and display recognition."""

    def __init__(
        self,
        capture_jpeg: Callable[[], bytes],
        recognize_display: Callable[[bytes], str],
        *,
        clock: Callable[[], float] = time.time,
        provider_name: str = "vision-llm",
    ) -> None:
        self._capture_jpeg = capture_jpeg
        self._recognize_display = recognize_display
        self._clock = clock
        self._provider_name = provider_name

    def read_weight(self) -> BalanceReading:
        jpeg = self._capture_jpeg()
        if not jpeg:
            raise RuntimeError("电子秤相机未返回图像")
        raw_text = self._recognize_display(jpeg).strip()
        match = _WEIGHT_PATTERN.search(raw_text)
        if match is None:
            raise ValueError(f"无法从电子秤识别结果中解析重量: {raw_text!r}")
        return BalanceReading(
            weight_g=float(match.group()),
            captured_at=self._clock(),
            provider=self._provider_name,
            raw_text=raw_text,
        )

    def close(self) -> None:
        """Injected collaborators own their own lifetimes."""


class SimulatedBalanceReader:
    def __init__(self, weight_g: float = 0.0) -> None:
        self._weight_g = weight_g

    def read_weight(self) -> BalanceReading:
        return BalanceReading(
            weight_g=self._weight_g,
            captured_at=time.time(),
            provider="simulation",
        )

    def close(self) -> None:
        pass
