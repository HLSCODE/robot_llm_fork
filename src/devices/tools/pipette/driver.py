"""ADP pipette protocol over one injected and explicitly owned transport."""

from __future__ import annotations

import logging
import math
import time

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
    def __init__(
        self,
        transport: Transport,
        *,
        motion_timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if not math.isfinite(motion_timeout_seconds) or motion_timeout_seconds <= 0:
            raise ValueError("ADP motion timeout must be finite and positive")
        if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
            raise ValueError("ADP poll interval must be finite and positive")
        self._transport = transport
        self._motion_timeout_seconds = motion_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

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
        self._exchange_ascii(function_code, value)
        return True

    def _exchange_ascii(
        self,
        function_code: str,
        value: int | None = None,
    ) -> bytes:
        """Return reply data after validating framing, CRC, address and function."""
        payload = self._create_command(function_code, value)
        logger.info(
            "ADP ASCII request: command=%s, request_hex=%s",
            function_code,
            payload.hex(),
        )
        try:
            response = self._transport.transact_with_strategy(
                ReadUntilStrategy(
                    payload,
                    terminator=b"\r\n",
                    max_size=20,
                )
            )
        except TransportError as exc:
            logger.warning(
                "ADP ASCII exchange failed: command=%s, request_hex=%s, "
                "category=%s, operation=%s, error=%s",
                function_code,
                payload.hex(),
                exc.category.value,
                exc.operation,
                exc,
            )
            raise
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
        return self._parse_ascii_reply(function_code, response)

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
        return self._execute_liquid_motion("n", volume_ul)

    def dispense(self, volume_ul: int) -> bool:
        return self._execute_liquid_motion("p", volume_ul)

    def dispense_all(self) -> bool:
        return self._execute_liquid_motion("p", 0)

    def query_pump_status(self) -> str:
        """Read the two-character pump state using the supplied ADP protocol."""
        data = self._exchange_ascii("d")
        if len(data) != 2 or any(byte not in b"0123456789abcdefABCDEF" for byte in data):
            raise ProtocolError(f"ADP pump status must be two hexadecimal digits: {data!r}")
        return data.decode("ascii").upper()

    def _execute_liquid_motion(self, function_code: str, volume_ul: int) -> bool:
        """Accept a motion only on 01, then wait until the pump reaches its target.

        Only status queries are repeated: replaying a liquid command could move
        the same volume twice if an acknowledgement or completion query is lost.
        """
        deadline = time.monotonic() + self._motion_timeout_seconds
        reply = self._exchange_ascii(function_code, volume_ul)
        if reply != b"01":
            code = reply.decode("ascii")
            reason = {
                "00": "pump is busy; command was not executed",
                "02": "volume limit exceeded; command was not executed",
            }.get(code, "missing or unknown motion acknowledgement")
            raise ProtocolError(
                f"ADP command {function_code} rejected: {reason} (code={code!r})",
                raw_error_code=code,
            )

        logger.info("ADP command %s accepted; waiting for pump completion", function_code)
        while True:
            self._check_motion_deadline(function_code, deadline)
            # Leave a gap after the motion acknowledgement as well as between
            # queries; a controller may still be transitioning into motion.
            time.sleep(min(self._poll_interval_seconds, max(0.0, deadline - time.monotonic())))
            self._check_motion_deadline(function_code, deadline)
            try:
                status = self.query_pump_status()
            except TransportError as exc:
                if (
                    exc.category is not TransportErrorCategory.TIMEOUT
                    or exc.operation not in ("read", "read_until")
                ):
                    raise
                self._check_motion_deadline(function_code, deadline)
                logger.warning(
                    "ADP command %s completion query timed out; retrying status query only; "
                    "remaining_seconds=%.3f",
                    function_code,
                    max(0.0, deadline - time.monotonic()),
                )
                continue
            self._check_motion_deadline(function_code, deadline)
            if status == "01":
                logger.info("ADP command %s completed: pump reached target", function_code)
                return True
            if status != "00":
                reason = {
                    "02": "abnormal collision",
                    "03": "liquid level detected before target was reached",
                    "05": "motion limit exceeded",
                    "06": "aspiration blockage",
                    "07": "dispensing blockage",
                    "09": "air aspiration alarm at start",
                    "0A": "air aspiration alarm at end",
                }.get(status, "unknown pump status")
                raise ProtocolError(
                    f"ADP command {function_code} failed: {reason} (status={status})",
                    raw_error_code=status,
                )

    def _check_motion_deadline(self, function_code: str, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise TransportError(
                f"ADP command {function_code}: motion completion timed out after "
                f"{self._motion_timeout_seconds:g}s",
                category=TransportErrorCategory.TIMEOUT,
                operation="wait_for_completion",
            )

    def eject_tip(self) -> bool:
        response = self._transport.transact_with_strategy(
            FixedLengthStrategy(_EJECT_TIP_COMMAND, 8)
        )
        return ModbusRTUProtocol().parse_write_register(
            response, address=1, register=0x0107, value=1,
        )

    def close(self) -> None:
        self._transport.close()
