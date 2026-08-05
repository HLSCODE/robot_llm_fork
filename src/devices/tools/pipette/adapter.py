from __future__ import annotations

from typing import Any


class PipetteAdapter:
    """Combine the ADP liquid controller and tip operations."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    def initialize(self) -> bool:
        return bool(self._controller.initialize())

    def set_absorb_speed(self, speed_ul_s: int) -> bool:
        return bool(self._controller.set_absorb_speed(speed_ul_s))

    def set_dispense_speed(self, speed_ul_s: int) -> bool:
        return bool(self._controller.set_dispense_speed(speed_ul_s))

    def absorb(self, volume_ul: int) -> bool:
        return bool(self._controller.absorb(volume_ul))

    def dispense(self, volume_ul: int) -> bool:
        return bool(self._controller.dispense(volume_ul))

    def dispense_all(self) -> bool:
        return bool(self._controller.dispense_all())

    def eject_tip(self) -> bool:
        return bool(self._controller.eject_tip())

    def close(self) -> None:
        self._controller.close()

    def enter_safe_state(self) -> None:
        if not self._controller.initialize():
            raise RuntimeError("pipette failed to return to initialized position")
