"""Bounded TCP transport. No robot operations or task logic live here."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import ssl
from typing import Any
from uuid import uuid4

from ...configuration.settings import ServerSettings
from ...application.saved_tasks import TaskServiceError
from ..contracts import ServerRequest, TaskApi
from ..access import ServerAccessError
from ..security import WebSocketRequestLimiter, create_server_ssl_context

logger = logging.getLogger(__name__)
_FIELDS = {
    "ping": set(),
    "authenticate": {"token"},
    "control.acquire": set(),
    "control.heartbeat": set(),
    "control.release": set(),
    "control.status": set(),
    "task.list": set(),
    "task.run": {"task"},
    "run.status": {"run_id"},
    "run.cancel": {"run_id"},
}


class RobotTcpServer:
    def __init__(
        self, api: TaskApi, settings: ServerSettings, *, authentication_configured: bool
    ) -> None:
        if not settings.tcp_host.strip() or not 0 <= settings.tcp_port <= 65535:
            raise ValueError("TCP host/port is invalid")
        for value in (
            settings.tcp_max_message_size_bytes,
            settings.tcp_max_connections,
            settings.tcp_max_requests_per_second,
            settings.tcp_idle_timeout_seconds,
            settings.tcp_send_timeout_seconds,
        ):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("TCP limits/timeouts must be positive and finite")
        self._api = api
        self._settings = settings
        self._ssl: ssl.SSLContext | None = create_server_ssl_context(
            settings.tcp_tls_certificate_path,
            settings.tcp_tls_private_key_path,
        )
        if settings.tcp_host.lower() not in {"127.0.0.1", "localhost", "::1"}:
            if self._ssl is None or not authentication_configured:
                raise ValueError("非本机 TCP 监听必须启用 TLS 和认证")
        self._server: asyncio.Server | None = None
        self._connections: dict[asyncio.Task[Any], asyncio.StreamWriter] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._limiter = WebSocketRequestLimiter(
            max_requests_per_second=settings.tcp_max_requests_per_second,
            max_concurrent_requests=settings.tcp_max_connections,
        )

    @property
    def name(self) -> str:
        return "tcp"

    @property
    def endpoint(self) -> str:
        port = (
            self._server.sockets[0].getsockname()[1]
            if self._server and self._server.sockets
            else self._settings.tcp_port
        )
        return f"{'tls+tcp' if self._ssl else 'tcp'}://{self._settings.tcp_host}:{port}"

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("TCP service is already running")
        self._server = await asyncio.start_server(
            self._handle,
            self._settings.tcp_host,
            self._settings.tcp_port,
            limit=self._settings.tcp_max_message_size_bytes + 1,
            ssl=self._ssl,
        )
        logger.info("TCP 服务已启动: %s", self.endpoint)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in tuple(self._connections.values()):
            writer.close()
        if self._connections:
            # A dispatch may already have accepted a task. Finish dispatch before
            # tearing down the loop; disconnect never cancels the robot task.
            tasks = tuple(self._connections)
            done, pending = await asyncio.wait(tasks, timeout=3)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("TCP 服务已停止")

    async def _send(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(
            (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        )
        await asyncio.wait_for(writer.drain(), self._settings.tcp_send_timeout_seconds)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        if len(self._connections) >= self._settings.tcp_max_connections:
            writer.close()
            return
        self._connections[task] = writer
        client_id = f"tcp:{uuid4().hex}"
        self._api.access.register(
            client_id, str(writer.get_extra_info("peername")), control_cleanup=False
        )
        try:
            while True:
                line = await asyncio.wait_for(
                    reader.readline(), self._settings.tcp_idle_timeout_seconds
                )
                if not line:
                    break
                if len(line) > self._settings.tcp_max_message_size_bytes or not line.endswith(
                    b"\n"
                ):
                    await self._send(
                        writer,
                        {
                            "ok": False,
                            "error": {"code": "invalid_frame", "message": "消息过长或缺少换行"},
                        },
                    )
                    break
                await self._request(client_id, line, writer)
        except (ConnectionError, TimeoutError, ValueError, asyncio.LimitOverrunError):
            logger.info("TCP connection closed: client_id=%s", client_id)
        finally:
            watcher = self._watchers.pop(client_id, None)
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            self._api.access.unregister(client_id)
            self._limiter.unregister(client_id)
            self._connections.pop(task, None)
            writer.close()
            try:
                await asyncio.wait_for(
                    writer.wait_closed(), self._settings.tcp_send_timeout_seconds
                )
            except (ConnectionError, TimeoutError):
                logger.debug("TCP close did not complete cleanly: %s", client_id)

    async def _request(self, client_id: str, line: bytes, writer: asyncio.StreamWriter) -> None:
        request_id: str | None = None
        admission = self._limiter.admit(client_id)
        try:
            if not admission.accepted:
                raise TaskServiceError(admission.code or "busy", "请求过于频繁或服务器繁忙")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeError, ValueError) as error:
                raise TaskServiceError("invalid_request", "请求必须是 UTF-8 JSON") from error
            if not isinstance(message, dict):
                raise TaskServiceError("invalid_request", "请求必须是 JSON 对象")
            request_id = message.get("request_id")
            if not isinstance(request_id, str) or not re.fullmatch(
                r"[A-Za-z0-9._:-]{1,128}", request_id
            ):
                request_id = None
                raise TaskServiceError("invalid_request", "缺少有效 request_id")
            if type(message.get("version")) is not int or message["version"] != 1:
                raise TaskServiceError("unsupported_version", "version 必须为 1")
            command = message.get("command")
            if not isinstance(command, str) or command not in _FIELDS:
                raise TaskServiceError("unknown_command", "不支持的指令")
            if set(message) - {"version", "request_id", "command"} - _FIELDS[command]:
                raise TaskServiceError("invalid_request", "存在不支持的字段")
            response = await asyncio.to_thread(
                self._api.dispatch,
                ServerRequest(
                    client_id,
                    request_id,
                    command,
                    message,
                    "tcp",
                ),
            )
            await self._send(
                writer, {"version": 1, "request_id": request_id, "ok": True, **response}
            )
            if command == "task.run":
                previous = self._watchers.pop(client_id, None)
                if previous is not None:
                    previous.cancel()
                    await asyncio.gather(previous, return_exceptions=True)
                principal = self._api.access.session(client_id).principal or client_id
                self._watchers[client_id] = asyncio.create_task(
                    self._watch_run(writer, response["run_id"], principal),
                    name=f"TCPRun-{client_id}",
                )
        except (TaskServiceError, ServerAccessError) as error:
            await self._send(
                writer,
                {
                    "version": 1,
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                },
            )
        except (ConnectionError, TimeoutError):
            raise
        except Exception:
            logger.exception(
                "TCP request failed: client_id=%s request_id=%s", client_id, request_id
            )
            await self._send(
                writer,
                {
                    "version": 1,
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": "internal_error", "message": "服务内部错误，请查看日志"},
                },
            )
        finally:
            if admission.accepted:
                self._limiter.release(client_id)

    async def _watch_run(self, writer: asyncio.StreamWriter, run_id: str, principal: str) -> None:
        previous = ""
        try:
            while not writer.is_closing():
                result = await asyncio.to_thread(self._api.tasks.get_run, run_id, principal)
                status = result["status"]
                terminal = status not in {"accepted", "running", "paused", "cancelling"}
                progress = result["result"].get("progress")
                marker = json.dumps([status, progress], sort_keys=True)
                if marker != previous:
                    event_name = (
                        "run.finished" if terminal else "run.progress" if progress else "run.status"
                    )
                    await self._send(writer, {"version": 1, "event": event_name, **result})
                    previous = marker
                if terminal:
                    return
                await asyncio.sleep(0.2)
        except (ConnectionError, TimeoutError):
            writer.close()
        except TaskServiceError:
            logger.warning("TCP run subscription ended: run_id=%s", run_id)
