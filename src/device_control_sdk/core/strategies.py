"""Serial exchange strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .exceptions import TransportError, TransportErrorCategory


class SerialExchangeStrategy(Protocol):
    def transact(self, serial_port) -> bytes: ...


def _write_all(serial_port, payload: bytes) -> None:
    serial_port.write(payload)
    serial_port.flush()


def _read_exact(serial_port, size: int) -> bytes:
    data = serial_port.read(size)
    if len(data) != size:
        raise TransportError(
            f"serial timeout: expected {size} bytes, got {len(data)}",
            category=TransportErrorCategory.TIMEOUT,
            operation="read",
        )
    return data


@dataclass
class FixedLengthStrategy:
    payload: bytes
    response_size: int

    def transact(self, serial_port) -> bytes:
        _write_all(serial_port, self.payload)
        return _read_exact(serial_port, self.response_size)


@dataclass
class WriteOnlyStrategy:
    payload: bytes

    def transact(self, serial_port) -> bytes:
        _write_all(serial_port, self.payload)
        return b""


@dataclass
class ReadUntilStrategy:
    payload: bytes
    terminator: bytes = b"\n"
    max_size: int = 256

    def transact(self, serial_port) -> bytes:
        _write_all(serial_port, self.payload)
        data = serial_port.read_until(self.terminator, self.max_size)
        if not data:
            raise TransportError(
                "serial timeout",
                category=TransportErrorCategory.TIMEOUT,
                operation="read_until",
            )
        return data


@dataclass
class ReadSomeStrategy:
    payload: bytes
    max_size: int
    min_size: int = 1
    response_delay_seconds: float = 0.0

    def transact(self, serial_port) -> bytes:
        if self.max_size <= 0:
            raise ValueError("max_size must be positive")
        if not 0 <= self.min_size <= self.max_size:
            raise ValueError("min_size must be in range 0..max_size")
        if self.response_delay_seconds < 0:
            raise ValueError("response_delay_seconds must not be negative")
        _write_all(serial_port, self.payload)
        if self.response_delay_seconds:
            import time

            time.sleep(self.response_delay_seconds)
        data = serial_port.read(self.max_size)
        if len(data) < self.min_size:
            raise TransportError(
                f"serial timeout: expected at least {self.min_size} bytes, "
                f"got {len(data)}",
                category=TransportErrorCategory.TIMEOUT,
                operation="read",
            )
        return data


@dataclass
class ModbusRTUStrategy(FixedLengthStrategy):
    pass

