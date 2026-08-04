from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import logging
from threading import RLock
from typing import Any, Generic, TypeVar

from .contracts import StoppableDevice
from .errors import normalize_device_error
from .models import (
    DeviceAlreadyRegisteredError,
    DeviceCapability,
    DeviceContractError,
    DeviceErrorCategory,
    DeviceInitializationError,
    DeviceNotRegisteredError,
    DeviceOperationError,
    DeviceSnapshot,
    DeviceSafeStateResult,
    DeviceSafeStateStatus,
    DeviceState,
    DeviceStopResult,
    DeviceStopStatus,
    StopMode,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")
_STOP_CAPABILITY_BY_MODE = {
    StopMode.QUICK: DeviceCapability.QUICK_STOP,
    StopMode.EMERGENCY: DeviceCapability.EMERGENCY_STOP,
}


@dataclass(frozen=True, slots=True)
class DeviceRegistration(Generic[T]):
    device_id: str
    capabilities: frozenset[DeviceCapability]
    factory: Callable[[], T]
    close: Callable[[T], None]
    enter_safe_state: Callable[[T], None] | None = None


@dataclass(slots=True)
class _DeviceRecord:
    registration: DeviceRegistration[Any]
    state: DeviceState = DeviceState.REGISTERED
    instance: Any = None
    error: str = ""
    error_category: str = ""
    raw_error_code: str = ""
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
            record.error_category = ""
            record.raw_error_code = ""
            try:
                instance = record.registration.factory()
                if instance is None:
                    raise DeviceInitializationError(
                        f"device '{device_id}' factory returned None"
                    )
            except Exception as exc:
                normalized = normalize_device_error(
                    exc,
                    device_id=device_id,
                    operation="device.initialize",
                    fallback_category=DeviceErrorCategory.UNAVAILABLE,
                )
                record.instance = None
                record.state = DeviceState.FAILED
                record.error = normalized.user_message
                record.error_category = normalized.category.value
                record.raw_error_code = normalized.raw_error_code
                raise normalized from exc

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
                error_category=record.error_category,
                raw_error_code=record.raw_error_code,
            )

    def snapshots(self) -> tuple[DeviceSnapshot, ...]:
        return tuple(
            self.snapshot(device_id)
            for device_id in self.registered_device_ids()
        )

    def declared_stop_modes(
        self,
        device_id: str,
    ) -> frozenset[StopMode]:
        """Return stop modes advertised by a device registration."""
        record = self._record(device_id)
        with record.lock:
            capabilities = record.registration.capabilities
            return frozenset(
                mode
                for mode, capability in _STOP_CAPABILITY_BY_MODE.items()
                if capability in capabilities
            )

    def stop_all(self, mode: StopMode) -> tuple[DeviceStopResult, ...]:
        """Stop every ready motion device without acquiring execution leases."""
        if not isinstance(mode, StopMode):
            raise TypeError("mode must be a StopMode")
        if mode is StopMode.CONTROLLED:
            raise ValueError(
                "controlled cancellation belongs to the application service"
            )
        required_capability = _STOP_CAPABILITY_BY_MODE[mode]
        motion_device_ids = self.find_by_capability(DeviceCapability.MOTION)
        return tuple(
            self._stop_device(device_id, mode, required_capability)
            for device_id in reversed(motion_device_ids)
        )

    def enter_safe_states(self) -> tuple[DeviceSafeStateResult, ...]:
        """Apply every ready device's explicitly registered safe-state policy."""
        device_ids = self.find_by_capability(DeviceCapability.SAFE_STATE)
        return tuple(
            self._enter_safe_state(device_id)
            for device_id in reversed(device_ids)
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
                normalized = normalize_device_error(
                    exc,
                    device_id=device_id,
                    operation="device.shutdown",
                )
                record.state = DeviceState.FAILED
                record.error = normalized.user_message
                record.error_category = normalized.category.value
                record.raw_error_code = normalized.raw_error_code
                raise normalized from exc
            finally:
                record.instance = None

            record.state = DeviceState.STOPPED
            record.error = ""
            record.error_category = ""
            record.raw_error_code = ""

    def shutdown_all(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        for device_id in reversed(self.registered_device_ids()):
            try:
                self.shutdown(device_id)
            except (DeviceInitializationError, DeviceOperationError) as exc:
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

    def _stop_device(
        self,
        device_id: str,
        mode: StopMode,
        required_capability: DeviceCapability,
    ) -> DeviceStopResult:
        record = self._record(device_id)
        with record.lock:
            if record.state is not DeviceState.READY:
                return DeviceStopResult(
                    device_id=device_id,
                    mode=mode,
                    status=DeviceStopStatus.NOT_READY,
                )
            if required_capability not in record.registration.capabilities:
                return DeviceStopResult(
                    device_id=device_id,
                    mode=mode,
                    status=DeviceStopStatus.UNSUPPORTED,
                    error=(
                        f"device does not advertise "
                        f"'{required_capability.value}'"
                    ),
                )

            instance = record.instance
            if not isinstance(instance, StoppableDevice):
                return DeviceStopResult(
                    device_id=device_id,
                    mode=mode,
                    status=DeviceStopStatus.FAILED,
                    error="advertised stop capability is not implemented",
                )
            if mode not in instance.supported_stop_modes:
                return DeviceStopResult(
                    device_id=device_id,
                    mode=mode,
                    status=DeviceStopStatus.FAILED,
                    error="adapter stop modes contradict registered capability",
                )
            try:
                instance.stop(mode)
            except Exception as exc:
                normalized = normalize_device_error(
                    exc,
                    device_id=device_id,
                    operation=f"device.stop.{mode.value}",
                )
                logger.warning(
                    "Device %s %s stop failed: %s",
                    device_id,
                    mode.value,
                    normalized.diagnostic_message,
                )
                return DeviceStopResult(
                    device_id=device_id,
                    mode=mode,
                    status=DeviceStopStatus.FAILED,
                    error=normalized.user_message,
                    error_category=normalized.category.value,
                    raw_error_code=normalized.raw_error_code,
                )
            return DeviceStopResult(
                device_id=device_id,
                mode=mode,
                status=DeviceStopStatus.STOPPED,
            )

    def _enter_safe_state(self, device_id: str) -> DeviceSafeStateResult:
        record = self._record(device_id)
        with record.lock:
            if record.state is not DeviceState.READY:
                return DeviceSafeStateResult(
                    device_id=device_id,
                    status=DeviceSafeStateStatus.NOT_READY,
                )
            action = record.registration.enter_safe_state
            if action is None:
                return DeviceSafeStateResult(
                    device_id=device_id,
                    status=DeviceSafeStateStatus.FAILED,
                    error="safe-state capability has no registered policy",
                )
            try:
                action(record.instance)
            except Exception as exc:
                normalized = normalize_device_error(
                    exc,
                    device_id=device_id,
                    operation="device.enter_safe_state",
                )
                logger.warning(
                    "Device %s safe state failed: %s",
                    device_id,
                    normalized.diagnostic_message,
                )
                return DeviceSafeStateResult(
                    device_id=device_id,
                    status=DeviceSafeStateStatus.FAILED,
                    error=normalized.user_message,
                    error_category=normalized.category.value,
                    raw_error_code=normalized.raw_error_code,
                )
            return DeviceSafeStateResult(
                device_id=device_id,
                status=DeviceSafeStateStatus.APPLIED,
            )
