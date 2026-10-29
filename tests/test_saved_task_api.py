from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.application import create_application_services
from src.application.saved_tasks import SavedTaskService, TaskRequestContext, TaskServiceError
from src.bootstrap.launcher import build_auxiliary_service_host
from src.configuration.settings import ApplicationSettings
from src.configuration.config_loader import load_application_settings
from src.configuration.toml_source import SCHEMA_VERSION
from src.configuration.config_validation import StartupOptions, validate_startup_configuration
from src.domain.models import ActionDefinition, ActionType
from src.domain.workflow import WorkflowDocument, WorkflowSequence, WorkflowActionNode
from src.persistence.task_run_journal import TaskRunJournal
from src.robot_server.contracts import TaskApi
from src.robot_server.security import ServerAccessController
from src.robot_server.tcp import RobotTcpServer
from src.robot_server.ws_server import RobotWebSocketServer


@pytest.fixture
def services(tmp_path):
    defaults = ApplicationSettings.defaults()
    settings = replace(defaults, data=replace(defaults.data, robot_data_dir=str(tmp_path / "data")))
    services = create_application_services(settings, simulation=True)
    profile = services.composition.robot_profile_id
    for name, seconds in (("demo", 0.1), ("long", 10)):
        document = WorkflowDocument(
            name,
            name,
            0,
            profile,
            WorkflowSequence(
                (
                    WorkflowActionNode(
                        "node",
                        "item",
                        ActionDefinition(
                            "wait",
                            "等待",
                            ActionType.WAIT,
                            {"wait_seconds": seconds},
                            robot_profile_id=profile,
                        ),
                    ),
                )
            ),
        )
        services.composition.save_workflow(name, document, origin="test")
    yield services
    if services.execution.snapshot().active:
        services.execution.cancel()
        services.execution.wait(3)


def context(request_id="one", principal="tester"):
    return TaskRequestContext(principal, request_id, "test")


def test_saved_run_deduplicates_and_does_not_change_editor(services):
    original = services.composition.sequence_entries()
    first = services.saved_tasks.run_task("demo", context())
    services.execution.wait(3)
    again = services.saved_tasks.run_task("demo", context())
    assert again["run_id"] == first["run_id"] and again["duplicate"]
    assert again["status"] == "succeeded"
    assert services.composition.sequence_entries() == original
    with pytest.raises(TaskServiceError, match="不同任务"):
        services.saved_tasks.run_task("long", context())
    with pytest.raises(TaskServiceError, match="不属于"):
        services.saved_tasks.get_run(first["run_id"], "other")
    with pytest.raises(TaskServiceError, match="不属于"):
        services.saved_tasks.cancel_run(first["run_id"], "other")


def test_duplicate_concurrent_requests_submit_once(services):
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(lambda _: services.saved_tasks.run_task("long", context()), range(2))
        )
    assert results[0]["run_id"] == results[1]["run_id"]
    assert sum(r["duplicate"] for r in results) == 1
    assert (
        services.saved_tasks.cancel_run(results[0]["run_id"], "tester")["status"]
        == "cancel_requested"
    )


def test_busy_and_invalid_paths_do_not_submit(services):
    services.saved_tasks.run_task("long", context())
    with pytest.raises(TaskServiceError) as caught:
        services.saved_tasks.run_task("demo", context("two"))
    assert caught.value.code == "busy"
    for task in ("../demo", "C:\\demo", "a/b", ""):
        with pytest.raises(TaskServiceError) as caught:
            services.saved_tasks.run_task(task, context("bad"))
        assert caught.value.code == "invalid_task"


def test_restart_marks_inflight_unknown_and_never_replays(tmp_path, services):
    path = tmp_path / "journal.sqlite3"
    journal = TaskRunJournal(path)
    journal.reserve("tester", "one", "demo", "old-run")
    restarted = SavedTaskService(
        services.composition,
        services.workflow_compiler,
        services.workflow_preflight,
        services.execution,
        TaskRunJournal(path),
    )
    result = restarted.run_task("demo", context())
    assert result["status"] == "unknown" and result["run_id"] == "old-run"
    assert not services.execution.snapshot().active


def test_journal_capacity_fails_before_motion(tmp_path, services):
    journal = TaskRunJournal(tmp_path / "bounded.sqlite3", max_records=1)
    journal.reserve("tester", "existing", "demo", "old")
    tasks = SavedTaskService(
        services.composition,
        services.workflow_compiler,
        services.workflow_preflight,
        services.execution,
        journal,
    )
    with pytest.raises(TaskServiceError) as caught:
        tasks.run_task("demo", context())
    assert caught.value.code == "history_full"
    assert not services.execution.snapshot().active


def test_nested_transport_configuration_and_env_secret(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        f"schema_version = {SCHEMA_VERSION}\n[server]\nsecurity_enabled=true\n"
        "[server.tcp]\nenabled=true\nport=9123\n[server.websocket]\nenabled=false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVER_AUTH_TOKEN", "test-secret")
    settings = load_application_settings(config)
    assert settings.server.tcp_enabled and settings.server.tcp_port == 9123
    assert not settings.server.websocket_enabled
    assert settings.secrets.server_auth_token == "test-secret"


def test_nested_includes_merge_field_by_field_and_entry_wins(tmp_path):
    (tmp_path / "first.toml").write_text(
        "[server.tcp]\nenabled=true\nport=9000\n", encoding="utf-8"
    )
    (tmp_path / "second.toml").write_text("[server.tcp]\nport=9001\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'schema_version={SCHEMA_VERSION}\ninclude=["first.toml","second.toml"]\n'
        "[server]\ntcp_port=9002\n",
        encoding="utf-8",
    )
    settings = load_application_settings(config)
    assert settings.server.tcp_enabled and settings.server.tcp_port == 9002


def test_tcp_configuration_reports_port_and_remote_security_errors(services):
    settings = replace(
        services.settings,
        server=replace(
            services.settings.server, tcp_enabled=True, tcp_host="0.0.0.0", tcp_port=8765
        ),
    )
    options = StartupOptions(True, True, "127.0.0.1", 8765, "INFO")
    codes = {i.code for i in validate_startup_configuration(settings, options).errors}
    assert {"insecure_tcp_binding", "server_port_conflict"} <= codes


def test_all_four_service_enable_combinations(services):
    for websocket, tcp in ((False, False), (True, False), (False, True), (True, True)):
        settings = replace(
            services.settings,
            server=replace(services.settings.server, websocket_enabled=websocket, tcp_enabled=tcp),
        )
        host = build_auxiliary_service_host(SimpleNamespace(), settings, services)
        assert {s.name for s in host.snapshots()} == ({"websocket"} if websocket else set()) | (
            {"tcp"} if tcp else set()
        )
        if websocket and tcp:
            ws, connection = (r.service for r in host._records)
            assert ws._access is connection._api.access


def test_allowlist_filters_listing_and_submission(tmp_path, services):
    tasks = SavedTaskService(
        services.composition,
        services.workflow_compiler,
        services.workflow_preflight,
        services.execution,
        TaskRunJournal(tmp_path / "allowed.sqlite3"),
        allowlist=("demo",),
    )
    assert tasks.list_tasks() == ("demo.workflow.json",)
    with pytest.raises(TaskServiceError) as caught:
        tasks.run_task("long", context())
    assert caught.value.code == "task_forbidden"


def test_lease_expired_by_other_transport_is_still_reported_to_owner_cleanup():
    clock = SimpleNamespace(now=0)
    access = ServerAccessController(
        "key", security_enabled=True, control_lease_seconds=1, clock=lambda: clock.now
    )
    for client in ("ws", "tcp"):
        access.register(client, "local")
        access.authenticate(client, "key")
    access.acquire_control("ws")
    clock.now = 2
    access.acquire_control("tcp")
    assert access.expire_control() == "ws"
    assert access.expire_control() is None
    assert access.control_snapshot().owner_client_id == "tcp"


def test_stateless_lease_expiry_does_not_request_other_adapter_cleanup():
    clock = SimpleNamespace(now=0)
    access = ServerAccessController(
        "", security_enabled=False, control_lease_seconds=1, clock=lambda: clock.now
    )
    access.register("tcp", "local", control_cleanup=False)
    access.acquire_control("tcp")
    clock.now = 2
    assert access.expire_control() is None
    assert access.control_snapshot() is None


@pytest.mark.parametrize(
    "line,code",
    [
        (b"not json\n", "invalid_request"),
        (b"[]\n", "invalid_request"),
        (b'{"version":true,"request_id":"x","command":"ping"}\n', "unsupported_version"),
        (b'{"version":1,"request_id":"x","command":"delete_all"}\n', "unknown_command"),
        (b'{"version":1,"request_id":"x","command":"ping","extra":1}\n', "invalid_request"),
        (b'{"version":1,"command":"ping"}\n', "invalid_request"),
    ],
)
def test_tcp_rejects_invalid_requests_without_dispatch(services, line, code):
    async def scenario():
        access = ServerAccessController("key", security_enabled=True, control_lease_seconds=30)
        api = TaskApi(services.saved_tasks, access)
        server = RobotTcpServer(
            api, replace(services.settings.server, tcp_port=0), authentication_configured=True
        )
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            with patch.object(api, "dispatch") as dispatch:
                writer.write(line)
                await writer.drain()
                response = json.loads(await asyncio.wait_for(reader.readline(), 2))
                assert not response["ok"] and response["error"]["code"] == code
                dispatch.assert_not_called()
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()

    asyncio.run(scenario())


def test_tcp_disconnect_does_not_cancel_accepted_task(services):
    async def scenario():
        access = ServerAccessController("", security_enabled=False, control_lease_seconds=30)
        api = TaskApi(services.saved_tasks, access)
        server = RobotTcpServer(
            api, replace(services.settings.server, tcp_port=0), authentication_configured=True
        )
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            for message in (
                {"version": 1, "request_id": "control", "command": "control.acquire"},
                {"version": 1, "request_id": "one", "command": "task.run", "task": "long"},
            ):
                writer.write((json.dumps(message) + "\n").encode())
                await writer.drain()
                response = json.loads(await asyncio.wait_for(reader.readline(), 2))
                assert response["ok"]
            run_id = response["run_id"]
            writer.close()
            await writer.wait_closed()
            await server.stop()
            assert services.execution.snapshot().active
            assert services.saved_tasks.get_run(run_id, "security-disabled")["status"] in {
                "accepted",
                "running",
            }
        finally:
            writer.close()
            await server.stop()

    asyncio.run(scenario())


def test_tcp_remote_bind_requires_tls_and_authentication(services):
    access = ServerAccessController("key", security_enabled=True, control_lease_seconds=30)
    with pytest.raises(ValueError, match="TLS"):
        RobotTcpServer(
            TaskApi(services.saved_tasks, access),
            replace(services.settings.server, tcp_host="0.0.0.0"),
            authentication_configured=True,
        )


def test_tcp_message_and_connection_limits_are_enforced(services):
    async def scenario():
        access = ServerAccessController("", security_enabled=False, control_lease_seconds=30)
        server = RobotTcpServer(
            TaskApi(services.saved_tasks, access),
            replace(
                services.settings.server,
                tcp_port=0,
                tcp_max_message_size_bytes=128,
                tcp_max_connections=1,
            ),
            authentication_configured=False,
        )
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        first_reader, first_writer = await asyncio.open_connection("127.0.0.1", port)
        second_writer = None
        try:
            first_writer.write(b'{"version":1,"request_id":"first","command":"ping"}\n')
            await first_writer.drain()
            assert json.loads(await first_reader.readline())["ok"]
            second_reader, second_writer = await asyncio.open_connection("127.0.0.1", port)
            assert await asyncio.wait_for(second_reader.read(), 2) == b""
            first_writer.write(b"x" * 1024 + b"\n")
            await first_writer.drain()
            assert await asyncio.wait_for(first_reader.read(), 2) == b""
        finally:
            first_writer.close()
            if second_writer is not None:
                second_writer.close()
            await server.stop()
        assert not access._sessions
        assert not server._connections and not server._watchers

    asyncio.run(scenario())


def test_tcp_start_failure_does_not_stop_other_service(services):
    async def scenario():
        access = ServerAccessController("", security_enabled=False, control_lease_seconds=30)
        api = TaskApi(services.saved_tasks, access)
        first = RobotTcpServer(
            api, replace(services.settings.server, tcp_port=0), authentication_configured=False
        )
        await first.start()
        port = first._server.sockets[0].getsockname()[1]
        second = RobotTcpServer(
            api, replace(services.settings.server, tcp_port=port), authentication_configured=False
        )
        try:
            with pytest.raises(OSError):
                await second.start()
            assert first._server.is_serving()
        finally:
            await second.stop()
            await first.stop()

    asyncio.run(scenario())


def test_tcp_roundtrip_split_frames_final_event_and_reconnect(services):
    async def scenario():
        access = ServerAccessController(
            "test-secret", security_enabled=True, control_lease_seconds=30
        )
        api = TaskApi(services.saved_tasks, access)
        settings = replace(services.settings.server, tcp_port=0)
        server = RobotTcpServer(api, settings, authentication_configured=True)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        async def send(command, request_id, **parameters):
            payload = {"version": 1, "request_id": request_id, "command": command, **parameters}
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), 3))

        try:
            response = await send("task.run", "denied", task="demo")
            assert not response["ok"] and response["error"]["code"] == "authentication_required"
            assert (await send("authenticate", "auth", token="test-secret"))["ok"]
            assert (await send("control.acquire", "control"))["ok"]
            response = await send("task.run", "one", task="demo")
            assert response["ok"] and response["status"] == "accepted"
            run_id = response["run_id"]
            while True:
                event = json.loads(await asyncio.wait_for(reader.readline(), 3))
                if event["event"] == "run.finished":
                    assert event["run_id"] == run_id and event["status"] == "succeeded"
                    break
            # Two requests in one write, with the first JSON itself split.
            writer.write(b'{"version":1,"request_id":"split",')
            await writer.drain()
            writer.write(b'"command":"ping"}\n{"version":1,"request_id":"next","command":"ping"}\n')
            await writer.drain()
            assert json.loads(await reader.readline())["request_id"] == "split"
            assert json.loads(await reader.readline())["request_id"] == "next"
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            assert (await send("authenticate", "reauth", token="test-secret"))["ok"]
            result = await send("run.status", "query", run_id=run_id)
            assert result["status"] == "succeeded"
            assert result["request_id"] == "query" and result["execution_request_id"] == "one"
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()

    asyncio.run(scenario())


def test_websocket_saved_task_uses_shared_service_and_control(services):
    class Socket:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(json.loads(message))

    async def scenario():
        access = ServerAccessController("key", security_enabled=True, control_lease_seconds=30)
        ws = RobotWebSocketServer(
            services, security_enabled=True, auth_token="key", access_controller=access
        )
        socket = Socket()
        client_id = ws._register_client(socket, "local")
        access.authenticate(client_id, "key")
        access.register("tcp:test", "local")
        access.authenticate("tcp:test", "key")
        access.acquire_control("tcp:test")
        request = {
            "api_version": "2.0",
            "action": "execute_task",
            "request_id": "one",
            "name": "demo",
        }
        await ws._dispatch(socket, request)
        assert socket.messages[-1]["code"] == "control_required"
        access.release_control("tcp:test")
        access.acquire_control(client_id)
        with patch.object(
            services.saved_tasks, "run_task", wraps=services.saved_tasks.run_task
        ) as run:
            await ws._dispatch(socket, request)
            assert run.call_count == 1
            accepted = next(p for p in socket.messages if p.get("event") == "accepted")
            await asyncio.to_thread(services.execution.wait, 3)
            await ws._dispatch(socket, request)
            assert socket.messages[-1]["run_id"] == accepted["run_id"]
            assert socket.messages[-1]["duplicate"]
        access.clear()

    asyncio.run(scenario())
