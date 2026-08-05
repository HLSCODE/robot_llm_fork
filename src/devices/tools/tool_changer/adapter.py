from __future__ import annotations

from typing import Any


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
