from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

WEBSOCKET_API_VERSION = "2.0"


class WebSocketErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_REQUEST_ID = "invalid_request_id"
    API_VERSION_REQUIRED = "api_version_required"
    UNSUPPORTED_API_VERSION = "unsupported_api_version"
    UNKNOWN_ACTION = "unknown_action"
    RATE_LIMITED = "rate_limited"
    SERVER_BUSY = "server_busy"
    REQUEST_FAILED = "request_failed"
    TELEOPERATION_FAILED = "teleoperation_failed"
    CAMERA_FAILED = "camera_failed"
    DATA_COLLECTION_FAILED = "data_collection_failed"
    INTERNAL_ERROR = "internal_error"


_ERROR_EVENT_DEFAULTS = {
    "error": WebSocketErrorCode.REQUEST_FAILED,
    "teleop_error": WebSocketErrorCode.TELEOPERATION_FAILED,
    "camera_error": WebSocketErrorCode.CAMERA_FAILED,
    "demo_record_error": WebSocketErrorCode.DATA_COLLECTION_FAILED,
}


@dataclass(frozen=True, slots=True)
class RequestCorrelation:
    client_id: str
    principal: str | None
    action: str
    request_id: str


@dataclass(slots=True)
class WebSocketRequestContext:
    correlation: RequestCorrelation
    run_id: str | None = None
    error_code: str | None = None
    response_count: int = 0
    initial_audit_recorded: bool = False

    def decorate(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload)
        decorated.setdefault(
            "request_id",
            self.correlation.request_id,
        )
        decorated.setdefault("action", self.correlation.action)
        if self.run_id is not None:
            decorated.setdefault("run_id", self.run_id)

        event = decorated.get("event")
        default_code = _ERROR_EVENT_DEFAULTS.get(event)
        if default_code is not None:
            code = str(decorated.setdefault("code", default_code.value))
            self.error_code = code
            if event != "error":
                decorated.setdefault("error_source", event)
                decorated["event"] = "error"
        elif event == "access_denied":
            code = decorated.get("code")
            if isinstance(code, str):
                self.error_code = code

        self.response_count += 1
        return decorated

    def execution_correlation(self) -> RequestCorrelation:
        return self.correlation
