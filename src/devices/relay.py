"""Two-channel relay protocol over an injected transport."""

from __future__ import annotations

from ..device_control_sdk import Transport, WriteOnlyStrategy


_COMMANDS = {
    (1, True): b"\x01\x06\x00\x00\x00\x01\x48\x0A",
    (1, False): b"\x01\x06\x00\x00\x00\x00\x89\xCA",
    (2, True): b"\x01\x06\x00\x01\x00\x01\x19\xCA",
    (2, False): b"\x01\x06\x00\x01\x00\x00\xD8\x0A",
}


class RelayController:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def set_channel(self, channel: int, enabled: bool) -> None:
        try:
            command = _COMMANDS[(channel, enabled)]
        except KeyError as exc:
            raise ValueError(f"unsupported relay channel: {channel}") from exc
        self._transport.transact_with_strategy(WriteOnlyStrategy(command))

    def close(self) -> None:
        self._transport.close()
