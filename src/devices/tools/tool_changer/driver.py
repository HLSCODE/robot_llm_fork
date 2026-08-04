"""Tool-changer serial protocol over an injected transport."""

from __future__ import annotations

import struct
from enum import Enum

from ...transports import FixedLengthStrategy, ProtocolError, Transport


class ToolChangerCommand(str, Enum):
    LOCK = "close"
    UNLOCK = "open"
    STATUS = "status"
    TEMPERATURE = "temp"
    POWER_ON = "power_on"
    POWER_OFF = "power_off"
    POWER_STATUS = "power_status"


_PAYLOADS = {
    ToolChangerCommand.LOCK: bytes((0x53, 0x26, 0x01, 0x01, 0x01)),
    ToolChangerCommand.UNLOCK: bytes((0x53, 0x26, 0x01, 0x01, 0x02)),
    ToolChangerCommand.STATUS: bytes((0x53, 0x26, 0x02, 0x01, 0x01)),
    ToolChangerCommand.TEMPERATURE: bytes((0x53, 0x26, 0x03, 0x01, 0x01)),
    ToolChangerCommand.POWER_ON: bytes((0x53, 0x26, 0x04, 0x01, 0x01)),
    ToolChangerCommand.POWER_OFF: bytes((0x53, 0x26, 0x04, 0x01, 0x02)),
    ToolChangerCommand.POWER_STATUS: bytes((0x53, 0x26, 0x05, 0x01, 0x01)),
}


class Kuaihuanshou:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def send_command(self, command_type: str) -> str | bool | int:
        try:
            command = ToolChangerCommand(command_type)
        except ValueError as exc:
            raise ValueError(
                f"unsupported tool changer command: {command_type}"
            ) from exc
        payload = _append_crc(_PAYLOADS[command])
        response = self._transport.transact_with_strategy(
            FixedLengthStrategy(payload, len(payload))
        )
        if len(response) < 5:
            raise ProtocolError(
                f"tool changer response is too short: {len(response)}"
            )
        if command is ToolChangerCommand.TEMPERATURE:
            return int(response[4])
        if command in {
            ToolChangerCommand.LOCK,
            ToolChangerCommand.UNLOCK,
            ToolChangerCommand.POWER_ON,
            ToolChangerCommand.POWER_OFF,
        }:
            return True
        if command is ToolChangerCommand.STATUS:
            return {1: "locked", 2: "unlocked"}.get(
                response[4],
                "unknown",
            )
        return {1: "on", 2: "off"}.get(response[4], "unknown")

    def close(self) -> None:
        self._transport.close()


def _append_crc(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (
                (crc >> 1) ^ 0xA001
                if crc & 0x0001
                else crc >> 1
            )
    return payload + struct.pack("<H", crc)
