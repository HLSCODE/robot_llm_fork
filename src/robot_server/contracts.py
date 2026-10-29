"""Transport-independent remote task API; adapters own framing and delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
from typing import Any

from ..application.saved_tasks import SavedTaskService, TaskRequestContext, TaskServiceError
from .access import ServerAccessController, ServerAccessLevel

audit_logger = logging.getLogger("security.remote")


@dataclass(frozen=True, slots=True)
class ServerRequest:
    client_id: str
    request_id: str
    command: str
    parameters: Mapping[str, Any]
    origin: str


class TaskApi:
    """Shared authorization and operations for TCP and future HTTP adapters.

    A single configured API key represents one principal (not multiple users).
    Per-client leases still prevent simultaneous controllers across protocols.
    """

    def __init__(self, tasks: SavedTaskService, access: ServerAccessController) -> None:
        self.tasks = tasks
        self.access = access

    def dispatch(self, request: ServerRequest) -> dict[str, Any]:
        try:
            result = self._dispatch(request)
        except Exception as error:
            audit_logger.warning(
                "%s",
                json.dumps(
                    {
                        "client_id": request.client_id,
                        "request_id": request.request_id,
                        "command": request.command,
                        "origin": request.origin,
                        "outcome": "rejected",
                        "code": getattr(error, "code", "internal_error"),
                    }
                ),
            )
            raise
        session = self.access.session(request.client_id)
        audit_logger.info(
            "%s",
            json.dumps(
                {
                    "client_id": request.client_id,
                    "principal": session.principal,
                    "request_id": request.request_id,
                    "command": request.command,
                    "origin": request.origin,
                    "outcome": "accepted",
                    "run_id": result.get("run_id"),
                }
            ),
        )
        return result

    def _dispatch(self, request: ServerRequest) -> dict[str, Any]:
        command = request.command
        parameters = request.parameters
        if command == "ping":
            return {"status": "ok"}
        if command == "authenticate":
            session = self.access.authenticate(request.client_id, parameters.get("token"))
            return {"authenticated": session.authenticated}
        if command == "control.acquire":
            return self.access.acquire_control(request.client_id).to_dict()
        if command == "control.heartbeat":
            return self.access.renew_control(request.client_id).to_dict()
        if command == "control.release":
            return {"released": self.access.release_control(request.client_id)}
        if command == "control.status":
            lease = self.access.control_snapshot()
            return {"lease": lease.to_dict() if lease is not None else None}
        level = (
            ServerAccessLevel.CONTROL
            if command in {"task.run", "run.cancel"}
            else ServerAccessLevel.AUTHENTICATED
        )
        session = self.access.authorize(request.client_id, level)
        principal = session.principal or request.client_id
        if command == "task.list":
            return {"tasks": list(self.tasks.list_tasks())}
        if command == "task.run":
            return self.tasks.run_task(
                parameters.get("task", ""),
                TaskRequestContext(
                    principal,
                    request.request_id,
                    request.origin,
                ),
            )
        if command in {"run.status", "run.cancel"}:
            run_id = parameters.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise TaskServiceError("invalid_request", "缺少 run_id")
            operation = self.tasks.get_run if command == "run.status" else self.tasks.cancel_run
            return operation(run_id, principal)
        raise TaskServiceError("unknown_command", "不支持的指令")
