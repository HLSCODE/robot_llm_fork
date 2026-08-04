from __future__ import annotations

from typing import Any


class RelayBankAdapter:
    """Expose numbered digital outputs without leaking relay method names."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    def set_channel(self, channel: int, enabled: bool) -> None:
        if channel not in (1, 2):
            raise ValueError(f"unsupported relay channel: {channel}")
        self._controller.set_channel(channel, enabled)

    def close(self) -> None:
        self._controller.close()

    def enter_safe_state(self) -> None:
        for channel in (1, 2):
            self.set_channel(channel, False)


class ToolChangerAdapter:
    """Expose lock state instead of serial command strings."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller

    def set_locked(self, locked: bool) -> None:
        command = "close" if locked else "open"
        self._controller.send_command(command)

    def close(self) -> None:
        self._controller.close()

    def enter_safe_state(self) -> None:
        self.set_locked(True)


class PipetteAdapter:
    """Combine the ADP liquid controller and tip operations."""

    def __init__(
        self,
        controller: Any,
    ) -> None:
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
