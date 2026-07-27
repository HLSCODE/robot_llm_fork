from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import logging
from threading import RLock
from typing import Any, Generic, TypeVar

from .models import (
    DeviceAlreadyRegisteredError,
    DeviceCapability,
    DeviceContractError,
    DeviceInitializationError,
    DeviceNotRegisteredError,
    DeviceSnapshot,
    DeviceState,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class DeviceRegistration(Generic[T]):
    device_id: str
    capabilities: frozenset[DeviceCapability]
    factory: Callable[[], T]
    close: Callable[[T], None]


@dataclass(slots=True)
class _DeviceRecord:
    registration: DeviceRegistration[Any]
    state: DeviceState = DeviceState.REGISTERED
    instance: Any = None
    error: str = ""
    lock: RLock = field(default_factory=RLock)


class DeviceRuntime:
    """Own every configured device instance and its lifecycle."""

    def __init__(self) -> None:
        self._records: dict[str, _DeviceRecord] = {}
        self._registration_order: list[str] = []
        self._lock = RLock()

    def register(self, registration: DeviceRegistration[Any]) -> None:
        device_id = registration.device_id.strip()
        if not device_id:
            raise ValueError("device_id must not be empty")

        with self._lock:
            if device_id in self._records:
                raise DeviceAlreadyRegisteredError(
                    f"device '{device_id}' is already registered"
                )
            self._records[device_id] = _DeviceRecord(registration=registration)
            self._registration_order.append(device_id)

    def registered_device_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._registration_order)

    def initialize(self, device_id: str) -> Any:
        record = self._record(device_id)
        with record.lock:
            if record.state is DeviceState.READY:
                return record.instance
            if record.state in (DeviceState.STARTING, DeviceState.STOPPING):
                raise DeviceInitializationError(
                    f"device '{device_id}' is currently {record.state.value}"
                )

            record.state = DeviceState.STARTING
            record.error = ""
            try:
                instance = record.registration.factory()
                if instance is None:
                    raise DeviceInitializationError(
                        f"device '{device_id}' factory returned None"
                    )
            except Exception as exc:
                record.instance = None
                record.state = DeviceState.FAILED
                record.error = str(exc)
                if isinstance(exc, DeviceInitializationError):
                    raise
                raise DeviceInitializationError(
                    f"initialize device '{device_id}' failed: {exc}"
                ) from exc

            record.instance = instance
            record.state = DeviceState.READY
            return instance

    def initialize_all(
        self,
        device_ids: Iterable[str] | None = None,
    ) -> dict[str, DeviceSnapshot]:
        selected = tuple(device_ids or self.registered_device_ids())
        for device_id in selected:
            self.initialize(device_id)
        return {device_id: self.snapshot(device_id) for device_id in selected}

    def require(self, device_id: str, expected_type: type[T] | None = None) -> T:
        instance = self.initialize(device_id)
        if expected_type is not None and not isinstance(instance, expected_type):
            raise DeviceContractError(
                f"device '{device_id}' does not implement "
                f"{expected_type.__name__}"
            )
        return instance

    def get_if_ready(self, device_id: str) -> Any | None:
        record = self._record(device_id)
        with record.lock:
            return record.instance if record.state is DeviceState.READY else None

    def find_by_capability(
        self,
        capability: DeviceCapability,
    ) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                device_id
                for device_id in self._registration_order
                if capability in self._records[device_id].registration.capabilities
            )

    def snapshot(self, device_id: str) -> DeviceSnapshot:
        record = self._record(device_id)
        with record.lock:
            return DeviceSnapshot(
                device_id=device_id,
                state=record.state,
                capabilities=tuple(
                    sorted(
                        record.registration.capabilities,
                        key=lambda capability: capability.value,
                    )
                ),
                error=record.error,
            )

    def snapshots(self) -> tuple[DeviceSnapshot, ...]:
        return tuple(
            self.snapshot(device_id)
            for device_id in self.registered_device_ids()
        )

    def shutdown(self, device_id: str) -> None:
        record = self._record(device_id)
        with record.lock:
            if record.state in (DeviceState.REGISTERED, DeviceState.STOPPED):
                record.state = DeviceState.STOPPED
                return
            if record.state is DeviceState.FAILED and record.instance is None:
                record.state = DeviceState.STOPPED
                return
            if record.state is DeviceState.STARTING:
                raise DeviceInitializationError(
                    f"device '{device_id}' is still starting"
                )

            instance = record.instance
            record.state = DeviceState.STOPPING
            try:
                if instance is not None:
                    record.registration.close(instance)
            except Exception as exc:
                record.state = DeviceState.FAILED
                record.error = str(exc)
                raise DeviceInitializationError(
                    f"shutdown device '{device_id}' failed: {exc}"
                ) from exc
            finally:
                record.instance = None

            record.state = DeviceState.STOPPED
            record.error = ""

    def shutdown_all(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        for device_id in reversed(self.registered_device_ids()):
            try:
                self.shutdown(device_id)
            except DeviceInitializationError as exc:
                logger.exception("Device shutdown failed: %s", device_id)
                errors[device_id] = str(exc)
        return errors

    def _record(self, device_id: str) -> _DeviceRecord:
        with self._lock:
            try:
                return self._records[device_id]
            except KeyError as exc:
                raise DeviceNotRegisteredError(
                    f"device '{device_id}' is not registered"
                ) from exc
