"""Map infrastructure failures to stable device-domain error semantics."""

from __future__ import annotations

from ..transports import (
    ProtocolError,
    TransportError,
    TransportErrorCategory,
)
from .arm_models import RobotOperationError
from .models import (
    DeviceContractError,
    DeviceErrorCategory,
    DeviceInitializationError,
    DeviceNotRegisteredError,
    DeviceOperationError,
)


_USER_MESSAGES = {
    DeviceErrorCategory.UNAVAILABLE: "设备当前不可用",
    DeviceErrorCategory.CONNECTION: "设备连接失败",
    DeviceErrorCategory.TIMEOUT: "设备响应超时",
    DeviceErrorCategory.PROTOCOL: "设备通信协议错误",
    DeviceErrorCategory.REJECTED: "设备拒绝执行操作",
    DeviceErrorCategory.IO: "设备通信失败",
    DeviceErrorCategory.INTERNAL: "设备操作失败",
}

_TRANSPORT_CATEGORIES = {
    TransportErrorCategory.DEPENDENCY: DeviceErrorCategory.UNAVAILABLE,
    TransportErrorCategory.OPEN_FAILED: DeviceErrorCategory.CONNECTION,
    TransportErrorCategory.CLOSED: DeviceErrorCategory.CONNECTION,
    TransportErrorCategory.TIMEOUT: DeviceErrorCategory.TIMEOUT,
    TransportErrorCategory.IO: DeviceErrorCategory.IO,
}


def normalize_device_error(
    error: Exception,
    *,
    device_id: str,
    operation: str,
    fallback_category: DeviceErrorCategory = DeviceErrorCategory.INTERNAL,
) -> DeviceOperationError:
    """Convert a concrete failure without exposing its diagnostic detail."""

    if isinstance(error, DeviceOperationError):
        return DeviceOperationError(
            device_id=device_id or error.device_id,
            operation=operation or error.operation,
            category=error.category,
            user_message=_user_message(
                error.category,
                device_id or error.device_id,
                operation or error.operation,
            ),
            diagnostic_message=error.diagnostic_message,
            raw_error_code=error.raw_error_code,
        )

    category = fallback_category
    raw_error_code = ""
    if isinstance(error, TransportError):
        category = _TRANSPORT_CATEGORIES[error.category]
        raw_error_code = error.category.value
    elif isinstance(error, ProtocolError):
        category = DeviceErrorCategory.PROTOCOL
    elif isinstance(error, RobotOperationError):
        category = DeviceErrorCategory.REJECTED
        raw_error_code = "" if error.code is None else str(error.code)
    elif isinstance(
        error,
        (
            DeviceInitializationError,
            DeviceNotRegisteredError,
            DeviceContractError,
        ),
    ):
        category = DeviceErrorCategory.UNAVAILABLE
    elif isinstance(error, TimeoutError):
        category = DeviceErrorCategory.TIMEOUT

    return DeviceOperationError(
        device_id=device_id,
        operation=operation,
        category=category,
        user_message=_user_message(category, device_id, operation),
        diagnostic_message=f"{type(error).__name__}: {error}",
        raw_error_code=raw_error_code,
    )


def _user_message(
    category: DeviceErrorCategory,
    device_id: str,
    operation: str,
) -> str:
    context = "，".join(
        part
        for part in (
            f"设备={device_id}" if device_id else "",
            f"操作={operation}" if operation else "",
        )
        if part
    )
    suffix = f"（{context}）" if context else ""
    return _USER_MESSAGES[category] + suffix
