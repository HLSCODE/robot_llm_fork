from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from threading import RLock
from types import TracebackType

from .protocol import (
    build_write_bytes_frame,
    build_write_frame,
    build_write_words_frame,
    format_frame,
)
from .transport import SerialConfig, SerialTransport, Transport

TraceCallback = Callable[[bytes, str], None]


def print_trace(frame: bytes, note: str = "") -> None:
    suffix = f"  # {note}" if note else ""
    print(f"TX: {format_frame(frame)}{suffix}")


class DgusClient:
    def __init__(
        self,
        transport: Transport,
        *,
        tx_delay: float = 0.05,
        trace: TraceCallback | None = None,
    ):
        self.transport = transport
        self.tx_delay = tx_delay
        self.trace = trace
        self._lock = RLock()

    @classmethod
    def open_serial(
        cls,
        port: str,
        baudrate: int = 115200,
        *,
        timeout: float = 0.5,
        write_timeout: float = 1.0,
        tx_delay: float = 0.05,
        trace: TraceCallback | None = None,
    ) -> "DgusClient":
        transport = SerialTransport(
            SerialConfig(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
                write_timeout=write_timeout,
            )
        )
        return cls(transport, tx_delay=tx_delay, trace=trace)

    def write_frame(self, frame: bytes, note: str = "") -> bytes:
        with self._lock:
            self.transport.write(frame)
            if self.trace:
                self.trace(frame, note)
            if self.tx_delay > 0:
                time.sleep(self.tx_delay)
        return frame

    def write_bytes(self, addr: int, values: Iterable[int], note: str = "") -> bytes:
        frame = build_write_bytes_frame(addr, values)
        return self.write_frame(frame, note)

    def write_words(self, addr: int, values: Iterable[int], note: str = "") -> bytes:
        frame = build_write_words_frame(addr, values)
        return self.write_frame(frame, note)

    def write_payload(self, addr: int, payload: bytes, note: str = "") -> bytes:
        frame = build_write_frame(addr, payload)
        return self.write_frame(frame, note)

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "DgusClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
