"""ADP pipette protocol over one injected and explicitly owned transport."""

from __future__ import annotations

import logging

from ...transports import (
    FixedLengthStrategy,
    ReadUntilStrategy,
    Transport,
    ModbusRTUProtocol,
    CRCError,
    ProtocolError,
    TransportError,
    TransportErrorCategory,
)


logger = logging.getLogger(__name__)

_EJECT_TIP_COMMAND = bytes(
    (0x01, 0x06, 0x01, 0x07, 0x00, 0x01, 0xF8, 0x37)
)


class ADP:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    @staticmethod
    def _cal_crc(payload: bytes) -> int:
        crc = 0xFFFF
        for byte in payload:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 0x0001 else crc >> 1
        return crc

    @staticmethod
    def _validate_speed(speed_ul_s: int | None) -> int | None:
        if speed_ul_s is None:
            return None
        speed = int(speed_ul_s)
        if not 1 <= speed <= 9999:
            raise ValueError(
                f"ADP speed must be in 1..9999 uL/s, got {speed}"
            )
        return speed

    def _create_command(
        self,
        function_code: str,
        value: int | None = None,
    ) -> bytes:
        data = f"{int(value):04X}" if value is not None else ""
        frame = f">01{function_code}{data}".encode("ascii")
        return frame + f"{self._cal_crc(frame):04X}".encode("ascii")

    def _send_ascii(
        self,
        function_code: str,
        value: int | None = None,
    ) -> bool:
        """Validate the serial reply without interpreting undocumented data fields."""
        payload = self._create_command(function_code, value)
        response = self._transport.transact_with_strategy(
            ReadUntilStrategy(
                payload,
                terminator=b"\r\n",
                max_size=20,
            )
        )
        if not response:
            raise TransportError(
                f"ADP command {function_code}: no reply received",
                category=TransportErrorCategory.TIMEOUT,
                operation="read",
            )
        logger.info(
            "ADP ASCII exchange: command=%s, request_hex=%s, response_hex=%s",
            function_code,
            payload.hex(),
            response.hex(),
        )
        self._parse_ascii_reply(function_code, response)
        return True

    def _parse_ascii_reply(self, function_code: str, response: bytes) -> bytes:
        if not response.endswith(b"\r\n"):
            raise ProtocolError("ADP reply is incomplete: missing CRLF terminator")
        frame = response[:-2]
        if len(frame) < 8:
            raise ProtocolError("ADP reply is too short")
        body, checksum = frame[:-4], frame[-4:]
        try:
            body.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError("ADP reply body is not ASCII") from exc
        if any(byte not in b"0123456789abcdefABCDEF" for byte in checksum):
            raise ProtocolError("ADP reply CRC is not four hexadecimal digits")
        if self._cal_crc(body) != int(checksum, 16):
            raise CRCError(f"ADP reply CRC mismatch: {response.hex()}")
        expected_header = f">01{function_code}".encode("ascii")
        if not body.startswith(expected_header):
            raise ProtocolError(
                f"ADP reply address/function mismatch: expected {expected_header!r}, "
                f"got {body[:4]!r}"
            )
        return body[len(expected_header):]

    def initialize(self) -> bool:
        return self._send_ascii("G")

    def set_absorb_speed(self, speed_ul_s: int | None) -> bool:
        speed = self._validate_speed(speed_ul_s)
        return True if speed is None else self._send_ascii("4", speed)

    def set_dispense_speed(self, speed_ul_s: int | None) -> bool:
        speed = self._validate_speed(speed_ul_s)
        return True if speed is None else self._send_ascii("B", speed)

    def absorb(self, volume_ul: int) -> bool:
        return self._send_ascii("n", volume_ul)

    def dispense(self, volume_ul: int) -> bool:
        return self._send_ascii("p", volume_ul)

    def dispense_all(self) -> bool:
        return self._send_ascii("p", 0)

    def eject_tip(self) -> bool:
        response = self._transport.transact_with_strategy(
            FixedLengthStrategy(_EJECT_TIP_COMMAND, 8)
        )
        return ModbusRTUProtocol().parse_write_register(
            response, address=1, register=0x0107, value=1,
        )

    def close(self) -> None:
        self._transport.close()
