"""Serial exchange strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .exceptions import TransportError


class SerialExchangeStrategy(Protocol):
    def transact(self, serial_port) -> bytes: ...


def _write_all(serial_port, payload: bytes) -> None:
    serial_port.write(payload)
    serial_port.flush()


def _read_exact(serial_port, size: int) -> bytes:
    data = serial_port.read(size)
    if len(data) != size:
        raise TransportError(f"serial timeout: expected {size} bytes, got {len(data)}")
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
            raise TransportError("serial timeout")
        return data


@dataclass
class ModbusRTUStrategy(FixedLengthStrategy):
    pass

