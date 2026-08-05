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
