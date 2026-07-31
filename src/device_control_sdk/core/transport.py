"""Serial transport wrapper."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .exceptions import TransportError, TransportErrorCategory
from .strategies import FixedLengthStrategy, SerialExchangeStrategy

DEFAULT_BAUDRATE = 115200
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_TIMEOUT = 0.5
MODBUS_RTU_INTER_FRAME_GAP_CHARS = 3.5


@dataclass(frozen=True, slots=True)
class SerialSettings:
    port: str
    baudrate: int = DEFAULT_BAUDRATE
    timeout_seconds: float = DEFAULT_TIMEOUT
    write_timeout_seconds: float | None = None
    bytesize: int = DEFAULT_BYTESIZE
    parity: str = DEFAULT_PARITY
    stopbits: int = DEFAULT_STOPBITS
    rts: bool | None = None
    dtr: bool | None = None
    open_attempts: int = 1
    open_retry_delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.port.strip():
            raise ValueError("serial port must not be empty")
        if self.baudrate <= 0:
            raise ValueError("serial baudrate must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("serial timeout must be positive")
        if (
            self.write_timeout_seconds is not None
            and self.write_timeout_seconds <= 0
        ):
            raise ValueError("serial write timeout must be positive")
        if self.open_attempts <= 0:
            raise ValueError("serial open attempts must be positive")
        if self.open_retry_delay_seconds < 0:
            raise ValueError(
                "serial open retry delay must not be negative"
            )


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
        port: str | SerialSettings,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
        bytesize: int = DEFAULT_BYTESIZE,
        parity: str = DEFAULT_PARITY,
        stopbits: int = DEFAULT_STOPBITS,
        write_timeout: float | None = None,
        rts: bool | None = None,
        dtr: bool | None = None,
        open_attempts: int = 1,
        open_retry_delay_seconds: float = 0.0,
        serial_factory: Callable[..., object] | None = None,
    ) -> None:
        try:
            import serial
        except ImportError as exc:
            raise TransportError(
                "pyserial is required",
                category=TransportErrorCategory.DEPENDENCY,
                operation="import",
            ) from exc

        settings = (
            port
            if isinstance(port, SerialSettings)
            else SerialSettings(
                port=port,
                baudrate=baudrate,
                timeout_seconds=timeout,
                write_timeout_seconds=write_timeout,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                rts=rts,
                dtr=dtr,
                open_attempts=open_attempts,
                open_retry_delay_seconds=open_retry_delay_seconds,
            )
        )
        self.settings = settings
        self.port = settings.port
        self.baudrate = settings.baudrate
        self.timeout = settings.timeout_seconds
        self._lock = threading.Lock()
        self._serial_factory = serial_factory or serial.Serial
        self._serial = self._open()

    def transact_with_strategy(self, strategy: SerialExchangeStrategy) -> bytes:
        with self._lock:
            if not bool(getattr(self._serial, "is_open", False)):
                raise TransportError(
                    f"serial transport is closed: {self.port}",
                    category=TransportErrorCategory.CLOSED,
                    port=self.port,
                    operation="transact",
                )
            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                time.sleep(_modbus_rtu_inter_frame_delay(self.baudrate))
                return strategy.transact(self._serial)
            except TransportError:
                raise
            except Exception as exc:
                raise TransportError(
                    str(exc),
                    category=TransportErrorCategory.IO,
                    port=self.port,
                    operation="transact",
                ) from exc

    def close(self) -> None:
        with self._lock:
            if bool(getattr(self._serial, "is_open", False)):
                self._serial.close()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return bool(getattr(self._serial, "is_open", False))

    def _open(self) -> object:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.open_attempts + 1):
            try:
                return self._open_once()
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.open_attempts:
                    time.sleep(self.settings.open_retry_delay_seconds)
        raise TransportError(
            f"open serial {self.port} failed after "
            f"{self.settings.open_attempts} attempt(s): {last_error}",
            category=TransportErrorCategory.OPEN_FAILED,
            port=self.port,
            operation="open",
        ) from last_error

    def _open_once(self) -> object:
        options = {
            "baudrate": self.settings.baudrate,
            "bytesize": self.settings.bytesize,
            "parity": self.settings.parity,
            "stopbits": self.settings.stopbits,
            "timeout": self.settings.timeout_seconds,
            "write_timeout": (
                self.settings.write_timeout_seconds
                or self.settings.timeout_seconds
            ),
        }
        if self.settings.rts is None and self.settings.dtr is None:
            return self._serial_factory(port=self.port, **options)
        serial_port = self._serial_factory(port=None, **options)
        if self.settings.rts is not None:
            setattr(serial_port, "rts", self.settings.rts)
        if self.settings.dtr is not None:
            setattr(serial_port, "dtr", self.settings.dtr)
        setattr(serial_port, "port", self.port)
        serial_port.open()
        return serial_port


def _modbus_rtu_inter_frame_delay(baudrate: int) -> float:
    bits_per_char = 11
    return MODBUS_RTU_INTER_FRAME_GAP_CHARS * bits_per_char / max(int(baudrate), 1)

