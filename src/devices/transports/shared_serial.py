"""One owner and transaction lock per physical serial port."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from threading import RLock
import time
from typing import Any, Callable

from .core.strategies import FixedLengthStrategy, SerialExchangeStrategy, SerialPort
from .core.transport import SerialSettings, SerialTransport, Transport


@dataclass
class _TimeoutExchange:
    strategy: SerialExchangeStrategy
    timeout: float

    def __getattr__(self, name: str) -> Any:
        return getattr(self.strategy, name)

    def transact(self, serial_port: SerialPort) -> bytes:
        port: Any = serial_port
        previous = getattr(port, "timeout", self.timeout)
        previous_write = getattr(port, "write_timeout", self.timeout)
        try:
            port.timeout = self.timeout
            port.write_timeout = self.timeout
            return self.strategy.transact(serial_port)
        finally:
            port.timeout = previous
            port.write_timeout = previous_write


class SharedSerialPool:
    """Only the pool closes physical handles; borrowers release references.

    Pool locking includes complete exchanges and bounds contention by each
    exchange's device timeout. No serial port is opened until an operation.
    """

    def __init__(self, factory: Callable[[SerialSettings], Transport] = SerialTransport) -> None:
        self._factory = factory
        self._lock = RLock()
        self._settings: dict[str, SerialSettings] = {}
        self._handles: dict[str, Transport] = {}
        self._locks: dict[str, RLock] = {}
        self._recover_at: dict[str, float] = {}
        self._borrowers: dict[str, int] = {}
        self._closed = False

    def borrow(self, settings: SerialSettings, *, open_now: bool = False) -> BorrowedSerial:
        key = os.path.normcase(os.path.realpath(settings.port))
        line = replace(
            settings,
            timeout_seconds=1,
            write_timeout_seconds=None,
            open_attempts=1,
            open_retry_delay_seconds=0,
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("共享末端连接池已关闭")
            existing = self._settings.get(key)
            if existing is not None and existing != line:
                raise ValueError(f"共享串口线路参数冲突: {settings.port}")
            self._settings[key] = line
            self._locks.setdefault(key, RLock())
            self._borrowers[key] = self._borrowers.get(key, 0) + 1
        endpoint = BorrowedSerial(self, key, settings)
        if open_now:
            try:
                with self._locks[key]:
                    if key not in self._handles:
                        self._handles[key] = self._factory(settings)
            except Exception:
                endpoint.close()
                raise
        return endpoint

    def release(self, key: str) -> None:
        with self._locks[key]:
            with self._lock:
                self._borrowers[key] -= 1
                last = self._borrowers[key] == 0
            if last:
                handle = self._handles.pop(key, None)
                self._recover_at.pop(key, None)
                if handle is not None:
                    handle.close()

    def reconnect(self, port: str) -> None:
        """Explicit recovery under the caller's connection resource lease."""
        key = os.path.normcase(os.path.realpath(port))
        lock = self._locks.get(key)
        if lock is None:
            return
        with lock:
            handle = self._handles.pop(key, None)
            self._recover_at.pop(key, None)
            if handle is not None:
                handle.close()

    def exchange(
        self, key: str, settings: SerialSettings, strategy: SerialExchangeStrategy
    ) -> bytes:
        with self._locks[key]:
            if self._closed:
                raise RuntimeError("共享末端连接池已关闭")
            # A timed-out request can have a late reply. Quarantine the channel
            # for one response timeout; SerialTransport flushes before sending.
            delay = self._recover_at.get(key, 0) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if key not in self._handles:
                self._handles[key] = self._factory(settings)
            try:
                return self._handles[key].transact_with_strategy(
                    _TimeoutExchange(strategy, settings.timeout_seconds)
                )
            except Exception:
                if isinstance(self._handles[key], SerialTransport):
                    self._recover_at[key] = time.monotonic() + settings.timeout_seconds
                raise

    def close(self) -> None:
        with self._lock:
            self._closed = True
        errors: list[Exception] = []
        for key, lock in tuple(self._locks.items()):
            with lock:
                handle = self._handles.pop(key, None)
                if handle is not None:
                    try:
                        handle.close()
                    except Exception as error:
                        errors.append(error)
        if errors:
            raise ExceptionGroup("共享末端串口关闭失败", errors)


class BorrowedSerial:
    def __init__(self, owner: SharedSerialPool, key: str, settings: SerialSettings) -> None:
        self._owner = owner
        self._key = key
        self.settings = settings
        self._closed = False

    def transact(self, payload: bytes, response_size: int) -> bytes:
        return self.transact_with_strategy(FixedLengthStrategy(payload, response_size))

    def transact_with_strategy(self, strategy: SerialExchangeStrategy) -> bytes:
        if self._closed:
            raise RuntimeError("借用的串口端点已关闭")
        return self._owner.exchange(self._key, self.settings, strategy)

    def close(self) -> None:
        """Only the last released endpoint may close an idle connection."""
        if not self._closed:
            self._closed = True
            self._owner.release(self._key)
