"""Exceptions for the local device control SDK."""


class DeviceControlSDKError(Exception):
    """Base SDK error."""


class StepperSDKError(DeviceControlSDKError):
    """Stepper motor error."""


class TransportError(DeviceControlSDKError):
    """Serial transport error."""


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

