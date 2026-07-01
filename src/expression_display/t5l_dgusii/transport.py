from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import serial


class Transport(Protocol):
    def write(self, frame: bytes) -> int:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class SerialConfig:
    port: str = "COM4"
    baudrate: int = 115200
    timeout: float = 0.5
    write_timeout: float = 1.0


class SerialTransport:
    def __init__(self, config: SerialConfig):
        self.config = config
        self._serial = serial.Serial(
            port=config.port,
            baudrate=config.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=config.timeout,
            write_timeout=config.write_timeout,
        )

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def write(self, frame: bytes) -> int:
        written = self._serial.write(frame)
        self._serial.flush()
        return written

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()

    def __enter__(self) -> "SerialTransport":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
