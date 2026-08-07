from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

from ...transports import (
    SerialSettings,
    SerialTransport as CoreSerialTransport,
    WriteOnlyStrategy,
)


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
        self._transport = CoreSerialTransport(
            SerialSettings(
                port=config.port,
                baudrate=config.baudrate,
                timeout_seconds=config.timeout,
                write_timeout_seconds=config.write_timeout,
            )
        )

    @property
    def is_open(self) -> bool:
        return self._transport.is_open

    def write(self, frame: bytes) -> int:
        self._transport.transact_with_strategy(
            WriteOnlyStrategy(frame)
        )
        return len(frame)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "SerialTransport":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
