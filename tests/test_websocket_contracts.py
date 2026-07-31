from __future__ import annotations

from typing import Any

import pytest

from src.robot_server.access_control import WebSocketAccessLevel
from src.robot_server.protocol import (
    ACTION_REQUEST_SCHEMAS,
    WEBSOCKET_API_VERSION,
    WebSocketErrorCode,
    WebSocketRequest,
    WebSocketRequestError,
    WebSocketResponse,
)
from src.robot_server.routing import WebSocketRoute, WebSocketRouteRegistry


# This explicit golden list is intentionally independent of the production
# schema builder. Adding, removing, or renaming a public action must update the
# protocol contract in the same change.
MINIMAL_VALID_PAYLOADS: dict[str, dict[str, Any]] = {
    "acquire_control": {},
    "add_to_sequence": {},
    "add_to_task": {"name": "task-a"},
    "ai_cancel": {},
    "ai_chat": {"text": "move to standby"},
    "ai_confirm": {"preview_id": "preview-1", "version": 1},
    "ai_status": {},
    "authenticate": {"token": "test-token"},
    "camera_status": {},
    "chat": {},
    "chat_connect": {},
    "chat_disconnect": {},
    "clear_sequence": {},
    "control_heartbeat": {},
    "control_status": {},
    "create_action": {
        "name": "wait",
        "type": "WAIT",
        "parameters": {"duration": 1.0},
    },
    "delete_action": {"id": "action-1"},
    "delete_task": {"name": "task-a"},
    "demo_record_start": {},
    "demo_record_stop": {},
    "demo_session_end": {},
    "demo_session_start": {"task": "pick-and-place"},
    "disconnect": {},
    "emergency_stop": {},
    "execute": {},
    "execute_task": {"name": "task-a"},
    "get_action_schema": {},
    "get_sequence": {},
    "get_task_detail": {"name": "task-a"},
    "init_body": {},
    "init_robots": {},
    "list_actions": {},
    "list_skills": {},
    "list_tasks": {},
    "load_task": {"name": "task-a"},
    "minicpm_status": {},
    "move_in_sequence": {"from": 0, "to": 1},
    "move_in_task": {"name": "task-a", "from": 0, "to": 1},
    "pause": {},
    "quick_stop": {},
    "release_control": {},
    "remove_from_sequence": {"index": 0},
    "remove_from_task": {"name": "task-a", "index": 0},
    "rename_task": {"name": "task-a", "new_name": "task-b"},
    "resume": {},
    "save_task": {"name": "task-a"},
    "server_metrics": {},
    "status": {},
    "stop": {},
    "subscribe_camera_frames": {},
    "teleop_init": {"joints": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
    "teleop_joint": {"joints": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
    "teleop_start": {},
    "teleop_stop": {},
    "test_camera": {},
    "unsubscribe_camera_frames": {},
    "update_action": {"id": "action-1"},
}

STABLE_ERROR_CODES = {
    "api_version_required",
    "camera_failed",
    "data_collection_failed",
    "internal_error",
    "invalid_payload",
    "invalid_request",
    "invalid_request_id",
    "rate_limited",
    "request_failed",
    "server_busy",
    "teleoperation_failed",
    "unknown_action",
    "unsupported_api_version",
}


def _request(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_version": WEBSOCKET_API_VERSION,
        "action": action,
        "request_id": f"contract:{action}",
        **payload,
    }


def test_golden_action_list_covers_every_public_schema() -> None:
    assert set(MINIMAL_VALID_PAYLOADS) == set(ACTION_REQUEST_SCHEMAS)


@pytest.mark.parametrize(
    ("action", "payload"),
    sorted(MINIMAL_VALID_PAYLOADS.items()),
)
def test_every_action_accepts_its_minimal_golden_request(
    action: str,
    payload: dict[str, Any],
) -> None:
    request = WebSocketRequest.parse(
        _request(action, payload),
        known_actions=set(ACTION_REQUEST_SCHEMAS),
    )

    assert request.action == action
    assert dict(request.payload) == payload


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("execute_task", {}),
        ("remove_from_sequence", {"index": True}),
        ("add_to_sequence", {"action_ids": ["action-1", 2]}),
        ("control_status", {"debug": True}),
    ],
)
def test_payload_contract_rejects_missing_wrong_and_unknown_fields(
    action: str,
    payload: dict[str, Any],
) -> None:
    with pytest.raises(WebSocketRequestError) as error:
        WebSocketRequest.parse(
            _request(action, payload),
            known_actions=set(ACTION_REQUEST_SCHEMAS),
        )

    assert error.value.code is WebSocketErrorCode.INVALID_PAYLOAD
    assert error.value.action == action
    assert error.value.request_id == f"contract:{action}"


def test_error_code_contract_is_unique_and_stable() -> None:
    values = [code.value for code in WebSocketErrorCode]

    assert len(values) == len(set(values))
    assert set(values) == STABLE_ERROR_CODES


@pytest.mark.parametrize("event", ["", 0, None])
def test_response_contract_requires_a_non_empty_string_event(event: object) -> None:
    with pytest.raises(ValueError):
        WebSocketResponse.from_payload({"event": event})


async def _handler(_websocket: Any, _request: WebSocketRequest) -> None:
    return None


def _route() -> WebSocketRoute:
    return WebSocketRoute(
        handler=_handler,
        access_level=WebSocketAccessLevel.PUBLIC,
    )


def test_route_registry_rejects_duplicate_action_ownership() -> None:
    registry = WebSocketRouteRegistry()
    registry.register({"status": _route()}, domain="device")

    with pytest.raises(ValueError, match="重复注册 action"):
        registry.register({"status": _route()}, domain="diagnostics")


def test_route_registry_rejects_actions_without_a_schema() -> None:
    registry = WebSocketRouteRegistry()

    with pytest.raises(ValueError, match="缺少请求 schema"):
        registry.register({"undocumented_action": _route()}, domain="diagnostics")
