from __future__ import annotations

from collections.abc import Coroutine, Mapping
from typing import Any, Protocol

from ...application import ApplicationServices


class WebSocketHandlerHost(Protocol):
    """Minimal transport/runtime capabilities consumed by domain handlers."""

    _services: ApplicationServices
    _host: str
    _port: int
    _ai_processing: bool
    _interaction_controller: Any
    _llm_registry: Any
    _llm_client: Any
    _planner_client: Any
    _minicpm_cfg: Any
    _minicpm_sessions: dict[int, dict[str, Any]]
    _ai_execution_pending: bool
    _execution_had_failure: bool
    _teleop_modes: dict[str, bool]
    _teleop_msg_counts: dict[str, int]
    _last_grip: dict[str, int | None]
    _demo_recorder: Any
    _demo_camera_session: Any
    _demo_session: dict[str, Any]
    _camera_preview_session: Any
    _camera_frame_subs: set[Any]
    _camera_push_task: Any
    _execution_requests: dict[str, Any]
    _execution_requests_lock: Any

    @property
    def _robot_system(self) -> Any: ...

    @property
    def _camera_manager(self) -> Any: ...

    def _json_msg(self, data: Mapping[str, Any]) -> str: ...

    def _composition_origin(self, websocket: Any) -> str: ...

    def _parse_sequence(self, raw: list[Any]) -> list[Any]: ...

    def _schedule_background_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str,
    ) -> Any: ...

    def _broadcast_threadsafe(self, data: dict[str, Any]) -> None: ...

    async def _broadcast(self, data: dict[str, Any]) -> None: ...

    async def _send_to_subscribers(
        self,
        data: dict[str, Any],
        subscribers: set[Any],
    ) -> None: ...

    def _audit(
        self,
        *,
        client_id: str,
        action: str,
        request_id: str,
        outcome: str,
        code: str | None = None,
        principal: str | None = None,
        run_id: str | None = None,
    ) -> None: ...

    async def _submit_execution(
        self,
        websocket: Any,
        sequence: list[Any],
        *,
        origin: str,
        message: str,
        steps: int,
    ) -> bool: ...
