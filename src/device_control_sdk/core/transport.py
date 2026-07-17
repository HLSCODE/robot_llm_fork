"""Serial transport wrapper."""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

from .exceptions import TransportError
from .strategies import FixedLengthStrategy, SerialExchangeStrategy

DEFAULT_BAUDRATE = 115200
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_TIMEOUT = 0.5
MODBUS_RTU_INTER_FRAME_GAP_CHARS = 3.5


@runtime_checkable
class Transport(Protocol):
    def transact(self, payload: bytes, response_size: int) -> bytes: ...
    def transact_with_strategy(self, strategy: SerialExchangeStrategy) -> bytes: ...
    def close(self) -> None: ...


class StrategyTransport:
    def transact(self, payload: bytes, response_size: int) -> bytes:
        return self.transact_with_strategy(FixedLengthStrategy(payload, response_size))

    def transact_with_strategy(self, strategy: SerialExchangeStrategy) -> bytes:
        raise NotImplementedError


class SerialTransport(StrategyTransport):
    def __init__(
        self,
        port: str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
        bytesize: int = DEFAULT_BYTESIZE,
        parity: str = DEFAULT_PARITY,
        stopbits: int = DEFAULT_STOPBITS,
    ) -> None:
        try:
            import serial
        except ImportError as exc:
            raise TransportError("pyserial is required") from exc

        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._lock = threading.Lock()
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=timeout,
                write_timeout=timeout,
            )
        except Exception as exc:
            raise TransportError(f"open serial {port} failed: {exc}") from exc

    def transact_with_strategy(self, strategy: SerialExchangeStrategy) -> bytes:
        with self._lock:
            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                time.sleep(_modbus_rtu_inter_frame_delay(self.baudrate))
                return strategy.transact(self._serial)
            except TransportError:
                raise
            except Exception as exc:
                raise TransportError(str(exc)) from exc

    def close(self) -> None:
        self._serial.close()


def _modbus_rtu_inter_frame_delay(baudrate: int) -> float:
    bits_per_char = 11
    return MODBUS_RTU_INTER_FRAME_GAP_CHARS * bits_per_char / max(int(baudrate), 1)

