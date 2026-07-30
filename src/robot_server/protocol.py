from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

WEBSOCKET_API_VERSION = "2.0"

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_COMMON_REQUEST_FIELDS = frozenset(
    {
        "api_version",
        "action",
        "request_id",
    }
)


class WebSocketErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_REQUEST_ID = "invalid_request_id"
    INVALID_PAYLOAD = "invalid_payload"
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
class RequestField:
    """A validated payload field accepted by one WebSocket action."""

    value_types: tuple[type, ...]
    required: bool = False
    item_types: tuple[type, ...] = ()
    allow_none: bool = False

    def validate(self, name: str, value: Any) -> None:
        if value is None and self.allow_none:
            return
        if not self._matches_type(value):
            expected = " | ".join(
                value_type.__name__ for value_type in self.value_types
            )
            raise WebSocketRequestError(
                WebSocketErrorCode.INVALID_PAYLOAD,
                f"字段 '{name}' 必须是 {expected}",
            )
        if self.item_types and isinstance(value, list):
            invalid_index = next(
                (
                    index
                    for index, item in enumerate(value)
                    if not isinstance(item, self.item_types)
                ),
                None,
            )
            if invalid_index is not None:
                expected = " | ".join(
                    item_type.__name__ for item_type in self.item_types
                )
                raise WebSocketRequestError(
                    WebSocketErrorCode.INVALID_PAYLOAD,
                    f"字段 '{name}[{invalid_index}]' 必须是 {expected}",
                )

    def _matches_type(self, value: Any) -> bool:
        if int in self.value_types and bool not in self.value_types:
            if isinstance(value, bool):
                return False
        return isinstance(value, self.value_types)


@dataclass(frozen=True, slots=True)
class ActionRequestSchema:
    """The complete payload contract for a single action."""

    fields: Mapping[str, RequestField] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def validate(self, payload: Mapping[str, Any]) -> None:
        unknown = sorted(set(payload) - set(self.fields))
        if unknown:
            raise WebSocketRequestError(
                WebSocketErrorCode.INVALID_PAYLOAD,
                f"action 不接受字段: {', '.join(unknown)}",
            )
        missing = sorted(
            name
            for name, spec in self.fields.items()
            if spec.required and name not in payload
        )
        if missing:
            raise WebSocketRequestError(
                WebSocketErrorCode.INVALID_PAYLOAD,
                f"缺少必填字段: {', '.join(missing)}",
            )
        for name, value in payload.items():
            self.fields[name].validate(name, value)


def _field(
    *value_types: type,
    required: bool = False,
    item_types: tuple[type, ...] = (),
    allow_none: bool = False,
) -> RequestField:
    return RequestField(
        value_types=value_types,
        required=required,
        item_types=item_types,
        allow_none=allow_none,
    )


def _schema(**fields: RequestField) -> ActionRequestSchema:
    return ActionRequestSchema(MappingProxyType(fields))


_EMPTY_SCHEMA = ActionRequestSchema()
_NO_PAYLOAD_ACTIONS = {
    "control_status",
    "acquire_control",
    "control_heartbeat",
    "release_control",
    "stop",
    "quick_stop",
    "emergency_stop",
    "pause",
    "resume",
    "list_actions",
    "get_action_schema",
    "get_sequence",
    "clear_sequence",
    "list_tasks",
    "ai_status",
    "list_skills",
    "status",
    "init_robots",
    "init_body",
    "disconnect",
    "test_camera",
    "camera_status",
    "subscribe_camera_frames",
    "unsubscribe_camera_frames",
    "chat_disconnect",
    "minicpm_status",
    "demo_record_start",
    "demo_record_stop",
    "demo_session_end",
}

ACTION_REQUEST_SCHEMAS: Mapping[str, ActionRequestSchema] = MappingProxyType(
    {
        **{action: _EMPTY_SCHEMA for action in _NO_PAYLOAD_ACTIONS},
        "authenticate": _schema(token=_field(str, required=True)),
        "execute": _schema(sequence=_field(list)),
        "execute_task": _schema(name=_field(str, required=True)),
        "create_action": _schema(
            name=_field(str, required=True),
            type=_field(str, required=True),
            parameters=_field(dict, required=True),
        ),
        "delete_action": _schema(id=_field(str, required=True)),
        "update_action": _schema(
            id=_field(str, required=True),
            name=_field(str),
            type=_field(str),
            parameters=_field(dict),
        ),
        "add_to_sequence": _schema(
            action_ids=_field(list, item_types=(str,)),
            items=_field(list),
        ),
        "remove_from_sequence": _schema(index=_field(int, required=True)),
        "move_in_sequence": _schema(
            **{
                "from": _field(int, required=True),
                "to": _field(int, required=True),
            }
        ),
        "save_task": _schema(name=_field(str, required=True)),
        "load_task": _schema(name=_field(str, required=True)),
        "delete_task": _schema(name=_field(str, required=True)),
        "get_task_detail": _schema(name=_field(str, required=True)),
        "rename_task": _schema(
            name=_field(str, required=True),
            new_name=_field(str, required=True),
        ),
        "add_to_task": _schema(
            name=_field(str, required=True),
            action_ids=_field(list, item_types=(str,)),
            items=_field(list),
            index=_field(int, allow_none=True),
        ),
        "remove_from_task": _schema(
            name=_field(str, required=True),
            index=_field(int, required=True),
        ),
        "move_in_task": _schema(
            **{
                "name": _field(str, required=True),
                "from": _field(int, required=True),
                "to": _field(int, required=True),
            }
        ),
        "ai_chat": _schema(text=_field(str, required=True)),
        "ai_confirm": _schema(
            preview_id=_field(str, required=True),
            version=_field(int, required=True),
            risk_acknowledged=_field(bool),
        ),
        "ai_cancel": _schema(
            preview_id=_field(str, allow_none=True),
            version=_field(int, allow_none=True),
        ),
        "chat_connect": _schema(provider=_field(str, allow_none=True)),
        "chat": _schema(
            provider=_field(str, allow_none=True),
            messages=_field(list),
            role=_field(str),
            content=_field(str, list),
            streaming=_field(bool),
            route_to_interaction=_field(bool),
            robot_interaction=_field(bool),
            temperature=_field(int, float),
            max_tokens=_field(int),
            max_new_tokens=_field(int),
            length_penalty=_field(int, float),
            image_max_slice_nums=_field(int),
            omni_mode=_field(bool),
            tts_enabled=_field(bool),
            tts=_field(bool),
            use_tts_template=_field(bool),
            enable_thinking=_field(bool),
        ),
        "teleop_init": _schema(
            arm=_field(str),
            joints=_field(list, dict, required=True),
        ),
        "teleop_start": _schema(
            arm=_field(str),
            arms=_field(list, item_types=(str,)),
        ),
        "teleop_joint": _schema(
            arm=_field(str),
            joints=_field(list, dict, required=True),
            follow=_field(bool),
            trajectory_mode=_field(int),
            grip=_field(int, dict),
        ),
        "teleop_stop": _schema(
            arm=_field(str),
            arms=_field(list, item_types=(str,)),
        ),
        "demo_session_start": _schema(
            task=_field(str, required=True),
            description=_field(str),
        ),
    }
)


class WebSocketRequestError(ValueError):
    def __init__(
        self,
        code: WebSocketErrorCode,
        message: str,
        *,
        action: str = "",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.action = action
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class WebSocketRequest(Mapping[str, Any]):
    """A versioned and action-schema-validated client request."""

    api_version: str
    action: str
    request_id: str
    payload: Mapping[str, Any]

    @classmethod
    def parse(
        cls,
        data: object,
        *,
        known_actions: set[str],
    ) -> WebSocketRequest:
        if not isinstance(data, dict):
            raise WebSocketRequestError(
                WebSocketErrorCode.INVALID_REQUEST,
                "请求必须是 JSON 对象",
            )

        action_value = data.get("action")
        action = action_value if isinstance(action_value, str) else ""
        request_id_value = data.get("request_id")
        request_id = (
            request_id_value
            if isinstance(request_id_value, str)
            and _REQUEST_ID_PATTERN.fullmatch(request_id_value)
            else None
        )
        if request_id is None:
            raise WebSocketRequestError(
                WebSocketErrorCode.INVALID_REQUEST_ID,
                "request_id 只能包含字母、数字、点、下划线、冒号或连字符，"
                "长度为 1..128",
                action=action,
            )

        api_version = data.get("api_version")
        if api_version != WEBSOCKET_API_VERSION:
            code = (
                WebSocketErrorCode.API_VERSION_REQUIRED
                if api_version is None
                else WebSocketErrorCode.UNSUPPORTED_API_VERSION
            )
            raise WebSocketRequestError(
                code,
                f"请求必须声明 api_version={WEBSOCKET_API_VERSION}",
                action=action,
                request_id=request_id,
            )
        if action not in known_actions:
            raise WebSocketRequestError(
                WebSocketErrorCode.UNKNOWN_ACTION,
                f"未知的 action: {action}",
                action=action,
                request_id=request_id,
            )

        # Production routes are checked by WebSocketRouteRegistry. The empty
        # fallback keeps explicitly injected in-process diagnostic routes
        # usable without weakening registered route validation.
        schema = ACTION_REQUEST_SCHEMAS.get(action, _EMPTY_SCHEMA)
        payload = {
            key: value
            for key, value in data.items()
            if key not in _COMMON_REQUEST_FIELDS
        }
        try:
            schema.validate(payload)
        except WebSocketRequestError as exc:
            raise WebSocketRequestError(
                exc.code,
                str(exc),
                action=action,
                request_id=request_id,
            ) from exc
        return cls(
            api_version=WEBSOCKET_API_VERSION,
            action=action,
            request_id=request_id,
            payload=MappingProxyType(payload),
        )

    def __getitem__(self, key: str) -> Any:
        if key == "api_version":
            return self.api_version
        if key == "action":
            return self.action
        if key == "request_id":
            return self.request_id
        return self.payload[key]

    def __iter__(self) -> Iterator[str]:
        yield from ("api_version", "action", "request_id")
        yield from self.payload

    def __len__(self) -> int:
        return len(self.payload) + 3


@dataclass(frozen=True, slots=True)
class WebSocketResponse:
    """A typed server response serialized by the transport boundary."""

    event: str
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> WebSocketResponse:
        event = payload.get("event")
        if not isinstance(event, str) or not event:
            raise ValueError("WebSocket response event must be a non-empty string")
        return cls(
            event=event,
            payload=MappingProxyType(
                {key: value for key, value in payload.items() if key != "event"}
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.event, **self.payload}


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

    def decorate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
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


CURRENT_WEBSOCKET_REQUEST: ContextVar[WebSocketRequestContext | None] = ContextVar(
    "websocket_request",
    default=None,
)
