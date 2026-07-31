"""Exceptions for the local device control SDK."""

from __future__ import annotations

from enum import Enum


class DeviceControlSDKError(Exception):
    """Base SDK error."""


class StepperSDKError(DeviceControlSDKError):
    """Stepper motor error."""


class TransportErrorCategory(str, Enum):
    DEPENDENCY = "dependency"
    OPEN_FAILED = "open_failed"
    CLOSED = "closed"
    TIMEOUT = "timeout"
    IO = "io"


class TransportError(DeviceControlSDKError):
    """Structured serial failure preserving endpoint and operation context."""

    def __init__(
        self,
        message: str,
        *,
        category: TransportErrorCategory = TransportErrorCategory.IO,
        port: str = "",
        operation: str = "",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.port = port
        self.operation = operation


class UnsupportedMotionModeError(StepperSDKError):
    """Unsupported stepper motion mode."""


class ProtocolError(DeviceControlSDKError):
    """Protocol parse/build error."""


class TransientReadingError(DeviceControlSDKError):
    """Temporary device reading error."""


class CRCError(ProtocolError):
    """CRC check failed."""


class ModbusException(ProtocolError):
    """Modbus exception response."""

