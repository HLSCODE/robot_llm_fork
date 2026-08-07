from __future__ import annotations

from dataclasses import dataclass, field
import time
from threading import RLock
from typing import Any

from .arm_names import normalize_arm_name


@dataclass
class VisionRelocalizationState:
    station_id: str
    arm: str
    marker_pose: list[list[float]]
    camera_name: str = ""
    image_path: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionContext:
    """Per-run state shared by actions in one sequence execution."""

    def __init__(self) -> None:
        self._vision_states: dict[tuple[str, str], VisionRelocalizationState] = {}
        self._lock = RLock()

    def set_vision_state(self, state: VisionRelocalizationState) -> None:
        key = (state.station_id, normalize_arm_name(state.arm))
        with self._lock:
            self._vision_states[key] = state

    def get_vision_state(self, station_id: str, arm: str) -> VisionRelocalizationState | None:
        with self._lock:
            return self._vision_states.get(
                (station_id, normalize_arm_name(arm))
            )

    def clear(self) -> None:
        with self._lock:
            self._vision_states.clear()
