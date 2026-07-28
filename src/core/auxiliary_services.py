from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import logging
from threading import Event, RLock, Thread, get_ident
from typing import Protocol


logger = logging.getLogger(__name__)


class AuxiliaryServiceState(str, Enum):
    REGISTERED = "registered"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AuxiliaryServiceSnapshot:
    name: str
    state: AuxiliaryServiceState
    endpoint: str
    error: str = ""

    @property
    def running(self) -> bool:
        return self.state is AuxiliaryServiceState.RUNNING


class AsyncAuxiliaryService(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def endpoint(self) -> str: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(slots=True)
class _ServiceRecord:
    service: AsyncAuxiliaryService
    state: AuxiliaryServiceState = AuxiliaryServiceState.REGISTERED
    error: str = ""
    stop_attempted: bool = False


class AuxiliaryServiceHost:
    """Own one background asyncio loop and optional network services."""

    def __init__(
        self,
        services: tuple[AsyncAuxiliaryService, ...],
        *,
        start_timeout_seconds: float,
        stop_timeout_seconds: float,
    ) -> None:
        if start_timeout_seconds <= 0:
            raise ValueError("service start timeout must be positive")
        if stop_timeout_seconds <= 0:
            raise ValueError("service stop timeout must be positive")

        names = tuple(service.name.strip() for service in services)
        if any(not name for name in names):
            raise ValueError("auxiliary service name must not be empty")
        if len(set(names)) != len(names):
            raise ValueError("auxiliary service names must be unique")

        self._records = tuple(
            _ServiceRecord(service=service)
            for service in services
        )
        self._start_timeout_seconds = start_timeout_seconds
        self._stop_timeout_seconds = stop_timeout_seconds
        self._lock = RLock()
        self._startup_completed = Event()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread_id: int | None = None

    @property
    def thread_id(self) -> int | None:
        with self._lock:
            return self._thread_id

    def start(self) -> tuple[AuxiliaryServiceSnapshot, ...]:
        if not self._records:
            return ()
        with self._lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    return self.snapshots()
                raise RuntimeError("auxiliary service host cannot be restarted")
            self._thread = Thread(
                target=self._thread_main,
                name="AuxiliaryServiceHost",
                daemon=False,
            )
            thread = self._thread

        try:
            thread.start()
        except Exception:
            self._mark_unfinished_failed("failed to start service host thread")
            raise

        total_timeout = (
            self._start_timeout_seconds * len(self._records) + 1.0
        )
        if not self._startup_completed.wait(total_timeout):
            raise TimeoutError(
                "auxiliary service host did not complete startup"
            )
        return self.snapshots()

    def stop(self) -> tuple[AuxiliaryServiceSnapshot, ...]:
        with self._lock:
            thread = self._thread
            loop = self._loop
        if thread is None or not thread.is_alive():
            return self.snapshots()
        if loop is None:
            raise RuntimeError("auxiliary service event loop is unavailable")

        total_timeout = (
            self._stop_timeout_seconds * len(self._records) + 1.0
        )
        future = asyncio.run_coroutine_threadsafe(
            self._stop_services(),
            loop,
        )
        try:
            future.result(timeout=total_timeout)
        except TimeoutError:
            future.cancel()
            logger.error("Auxiliary services exceeded shutdown timeout")
        finally:
            loop.call_soon_threadsafe(loop.stop)

        thread.join(timeout=total_timeout)
        if thread.is_alive():
            raise TimeoutError("auxiliary service host thread did not stop")
        return self.snapshots()

    def snapshots(self) -> tuple[AuxiliaryServiceSnapshot, ...]:
        with self._lock:
            return tuple(
                AuxiliaryServiceSnapshot(
                    name=record.service.name,
                    state=record.state,
                    endpoint=record.service.endpoint,
                    error=record.error,
                )
                for record in self._records
            )

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._thread_id = get_ident()
        try:
            loop.run_until_complete(self._start_services())
            self._startup_completed.set()
            loop.run_forever()
        except Exception as exc:
            logger.exception("Auxiliary service host failed")
            self._mark_unfinished_failed(str(exc))
            self._startup_completed.set()
        finally:
            try:
                loop.run_until_complete(self._stop_services())
                self._cancel_pending_tasks(loop)
            finally:
                loop.close()
                with self._lock:
                    self._loop = None

    async def _start_services(self) -> None:
        for record in self._records:
            self._update_record(
                record,
                state=AuxiliaryServiceState.STARTING,
                error="",
            )
            try:
                await asyncio.wait_for(
                    record.service.start(),
                    timeout=self._start_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "Auxiliary service %s failed to start: %s",
                    record.service.name,
                    exc,
                )
                await self._cleanup_failed_start(record, exc)
                continue
            self._update_record(
                record,
                state=AuxiliaryServiceState.RUNNING,
                error="",
            )

    async def _cleanup_failed_start(
        self,
        record: _ServiceRecord,
        start_error: Exception,
    ) -> None:
        record.stop_attempted = True
        error = str(start_error)
        try:
            await asyncio.wait_for(
                record.service.stop(),
                timeout=self._stop_timeout_seconds,
            )
        except Exception as cleanup_error:
            error = f"{error}; cleanup failed: {cleanup_error}"
        self._update_record(
            record,
            state=AuxiliaryServiceState.FAILED,
            error=error,
        )

    async def _stop_services(self) -> None:
        for record in reversed(self._records):
            if record.stop_attempted:
                continue
            record.stop_attempted = True
            if record.state is AuxiliaryServiceState.REGISTERED:
                self._update_record(
                    record,
                    state=AuxiliaryServiceState.STOPPED,
                    error="",
                )
                continue

            self._update_record(
                record,
                state=AuxiliaryServiceState.STOPPING,
                error=record.error,
            )
            try:
                await asyncio.wait_for(
                    record.service.stop(),
                    timeout=self._stop_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "Auxiliary service %s failed to stop: %s",
                    record.service.name,
                    exc,
                )
                self._update_record(
                    record,
                    state=AuxiliaryServiceState.FAILED,
                    error=str(exc),
                )
                continue
            self._update_record(
                record,
                state=AuxiliaryServiceState.STOPPED,
                error="",
            )

    def _update_record(
        self,
        record: _ServiceRecord,
        *,
        state: AuxiliaryServiceState,
        error: str,
    ) -> None:
        with self._lock:
            record.state = state
            record.error = error

    def _mark_unfinished_failed(self, error: str) -> None:
        with self._lock:
            for record in self._records:
                if record.state in {
                    AuxiliaryServiceState.RUNNING,
                    AuxiliaryServiceState.STOPPED,
                    AuxiliaryServiceState.FAILED,
                }:
                    continue
                record.state = AuxiliaryServiceState.FAILED
                record.error = error

    @staticmethod
    def _cancel_pending_tasks(
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        pending = tuple(
            task
            for task in asyncio.all_tasks(loop)
            if not task.done()
        )
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
