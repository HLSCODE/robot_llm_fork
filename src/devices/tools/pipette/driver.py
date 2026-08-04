"""ADP pipette protocol over one injected and explicitly owned transport."""

from __future__ import annotations

from ...transports import (
    FixedLengthStrategy,
    ReadSomeStrategy,
    Transport,
)


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
        self._transport.transact_with_strategy(
            ReadSomeStrategy(
                self._create_command(function_code, value),
                max_size=20,
                min_size=1,
            )
        )
        return True

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
        if response[:2] != b"\x01\x06":
            raise RuntimeError(
                f"unexpected pipette eject response: {response.hex()}"
            )
        return True

    def close(self) -> None:
        self._transport.close()
