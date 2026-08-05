"""Runtime adapter for the current mobile-base TCP product."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class MobileBaseProtocolClient(Protocol):
    @property
    def connected(self) -> bool: ...
    def connect(self) -> None: ...
    def send_command(self, command: Mapping[str, Any]) -> None: ...
    def receive_response(self) -> dict[str, Any]: ...
    def close(self) -> None: ...


class TcpMobileBaseAdapter:
    """Translate the MobileBase contract into the vendor TCP protocol."""

    def __init__(self, client: MobileBaseProtocolClient) -> None:
        self._client = client
        self._last_result: Any = None

    def connect(self) -> None:
        self._client.connect()

    def move_to_position(self, location_id: int, coordinate_id: int) -> bool:
        return self._execute(
            {"cmd": 1, "id": location_id, "cid": coordinate_id},
            expected_command=1,
        )

    def move_slowly(self, x: float, y: float, angle: float) -> bool:
        return self._execute(
            {"cmd": 2, "x": x, "y": y, "angle": angle},
            expected_command=2,
        )

    def get_last_result(self) -> Any:
        return self._last_result

    def close(self) -> None:
        self._client.close()

    def _execute(
        self,
        command: Mapping[str, Any],
        *,
        expected_command: int,
    ) -> bool:
        if not self._client.connected:
            self._client.connect()
        self._client.send_command(command)
        while True:
            response = self._client.receive_response()
            if "execute" in response:
                logger.debug("mobile-base status update: %s", response)
                continue
            if "result" not in response:
                raise ValueError("mobile-base response lacks result")
            if response.get("cmd") != expected_command:
                raise ValueError(
                    "mobile-base response command does not match request"
                )
            self._last_result = response["result"]
            return bool(self._last_result)


__all__ = ["MobileBaseProtocolClient", "TcpMobileBaseAdapter"]
