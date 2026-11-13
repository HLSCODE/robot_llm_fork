from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from src.application import create_application_services
from src.application.saved_tasks import TaskRequestContext
from src.configuration.settings import ApplicationSettings
from src.domain.execution_plan import ExecutionPlan
from src.domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    SequenceItem,
)
from src.domain.outputs import NodeOutput, json_output
from src.domain.workflow import WorkflowDocument, instantiate_subworkflow
from src.execution import ActionHandlerResult, ExecutionEventType
from src.execution.outputs import OutputCollector
from src.domain.execution_plan import ExecutionStepIdentity
from src.robot_server.contracts import TaskApi
from src.robot_server.security import ServerAccessController
from src.robot_server.tcp import RobotTcpServer
from src.robot_server.ws_server import RobotWebSocketServer
from src.vision.models import VisionPipelineResult
from src.persistence.task_run_journal import TaskRunJournal


@pytest.fixture
def services(tmp_path):
    defaults = ApplicationSettings.defaults()
    settings = replace(
        defaults,
        data=replace(defaults.data, robot_data_dir=str(tmp_path / "data")),
        vision=replace(
            defaults.vision,
            vision_debug_save_dir=str(tmp_path / "vision"),
            vision_relocalization_stations_file=str(tmp_path / "stations.json"),
        ),
    )
    services = create_application_services(settings, simulation=True)
    yield services
    if services.execution.snapshot().active:
        services.execution.cancel()
        services.execution.wait(3)


def item(services, name="measure", output=None, action_type=ActionType.WAIT):
    return SequenceItem(
        name,
        ActionDefinition(
            name,
            name,
            action_type,
            {"wait_seconds": 0.1}
            if action_type is ActionType.WAIT
            else {"action_mode": "teach", "arm": "left", "station_name": "test-station"},
            robot_profile_id=services.composition.robot_profile_id,
        ),
        output=output,
    )


def document(services, entries):
    return WorkflowDocument.from_entries(
        workflow_id="measure",
        name="measure",
        revision=0,
        robot_profile_id=services.composition.robot_profile_id,
        entries=entries,
        allow_remote=True,
    )


@pytest.mark.parametrize(
    "value",
    [
        {"bad": float("nan")},
        {"bad": float("inf")},
        {"bad": object()},
        {"bad": b"image"},
        {"bad": (1, 2)},
        {1: "key"},
        {"large": "x" * 65536},
    ],
)
def test_invalid_output_never_silently_stringified(value):
    with pytest.raises(ValueError):
        ActionHandlerResult.succeeded(outputs=value)


def test_json_validation_detaches_and_accepts_null():
    original = {"detected": False, "height": None, "values": [1, True, 2.5]}
    result = ActionHandlerResult.succeeded(outputs=original)
    original["values"].append(10)
    assert result.outputs["values"] == [1, True, 2.5]
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError):
        json_output(cycle)


def test_json_pointer_supports_nested_arrays_and_escaped_keys():
    value = {"a/b": {"~x": [None, {"height": 0}]}}
    assert NodeOutput("height", "/a~1b/~0x/1/height").select(value) == 0
    assert NodeOutput("nullable", "/a~1b/~0x/0").select(value) is None


@pytest.mark.parametrize(
    "settings",
    [
        {"name": "bad name"},
        {"name": "1bad"},
        {"name": "ok", "field": "height"},
        {"name": "ok", "field": "/bad~2"},
        {"name": "ok", "mode": "random"},
        {"name": "ok", "required": "yes"},
    ],
)
def test_invalid_export_settings(settings):
    with pytest.raises(ValueError):
        NodeOutput(**settings)


def test_export_roundtrip_compilation_and_subworkflow_clone(services):
    original = item(services, output=NodeOutput("tube", "/height"))
    workflow = document(services, [original])
    loaded = WorkflowDocument.from_dict(workflow.to_dict())
    assert loaded.to_entries()[0].output == original.output
    assert services.workflow_compiler.compile(loaded).entries[0].output == original.output
    assert instantiate_subworkflow(loaded).items[0].output == original.output
    assert "output" not in original.definition.to_dict()
    assert "output" not in item(services).to_dict()


def test_duplicate_names_and_single_in_loop_rejected_before_execution(services):
    duplicate = document(
        services, [item(services, "a", NodeOutput("tube")), item(services, "b", NodeOutput("tube"))]
    )
    with pytest.raises(ValueError, match="输出名称重复"):
        services.workflow_compiler.compile(duplicate)
    loop = document(services, [LoopBlock("loop", [item(services, output=NodeOutput("tube"))], 2)])
    with pytest.raises(ValueError, match="循环内输出"):
        services.workflow_compiler.compile(loop)
    assert not services.execution.snapshot().active


def test_selected_output_reaches_execution_snapshot_and_terminal_event(services):
    plan = ExecutionPlan.from_entries([item(services, output=NodeOutput("tube", "/height"))])
    events = []
    engine = services.execution._manager._engine
    with patch.object(
        engine._handler_registry,
        "execute",
        return_value=ActionHandlerResult.succeeded(
            outputs={"height": 23.6, "private_unselected": "not exported"}
        ),
    ):
        handle = services.execution.start(plan, origin="test", listener=events.append)
        result = handle.wait(3)
    assert result.outputs == {"tube": 23.6} and result.outputs_complete
    assert events[-1].data["outputs"] == {"tube": 23.6}
    result.outputs["tube"] = -1
    assert services.execution.snapshot().outputs["tube"] == 23.6


@pytest.mark.parametrize("mode,expected", [("last", 2), ("all", [1, 2])])
def test_loop_output_preserves_iterations(services, mode, expected):
    node = item(services, output=NodeOutput("tube", "/height", mode))
    plan = ExecutionPlan.from_entries([LoopBlock("loop", [node], 2)])
    engine = services.execution._manager._engine
    with patch.object(
        engine._handler_registry,
        "execute",
        side_effect=[
            ActionHandlerResult.succeeded(outputs={"height": 1}),
            ActionHandlerResult.succeeded(outputs={"height": 2}),
        ],
    ):
        result = services.execution.start(plan, origin="test").wait(3)
    if mode == "all":
        values = result.outputs["tube"]
        assert [entry["value"] for entry in values] == expected
        assert [entry["source"]["loop_iteration"] for entry in values] == [1, 2]
        assert len({entry["source"]["path"] for entry in values}) == 2
    else:
        assert result.outputs["tube"] == expected


def test_parallel_collector_is_deterministic_and_keeps_branch_identity(services):
    plan = ExecutionPlan.from_entries(
        [
            ParallelBlock(
                "parallel",
                [
                    ParallelBranch("a", [item(services, "a", NodeOutput("first", mode="all"))]),
                    ParallelBranch("b", [item(services, "b", NodeOutput("second", mode="all"))]),
                ],
            )
        ]
    )
    with patch.object(
        services.execution._manager._engine._handler_registry,
        "execute",
        return_value=ActionHandlerResult.succeeded(outputs={"height": 1}),
    ):
        result = services.execution.start(plan, origin="test").wait(3)
    assert result.outputs["first"][0]["source"]["branch_id"] == "a"
    assert result.outputs["second"][0]["source"]["branch_id"] == "b"


def test_failure_and_missing_required_keep_partial_output(services):
    plan = ExecutionPlan.from_entries(
        [item(services, "a", NodeOutput("first")), item(services, "b", NodeOutput("second"))]
    )
    engine = services.execution._manager._engine
    with patch.object(
        engine._handler_registry,
        "execute",
        side_effect=[
            ActionHandlerResult.succeeded(outputs={"height": 1}),
            ActionHandlerResult.succeeded(),
        ],
    ):
        result = services.execution.start(plan, origin="test").wait(3)
    assert result.error_code == "invalid_output"
    assert result.outputs == {"first": {"height": 1}}
    assert not result.outputs_complete


def test_optional_missing_and_no_export_do_not_leak_data(services):
    plan = ExecutionPlan.from_entries(
        [
            item(services, "a", NodeOutput("optional", "/missing", required=False)),
            item(services, "b"),
        ]
    )
    engine = services.execution._manager._engine
    with patch.object(
        engine._handler_registry,
        "execute",
        return_value=ActionHandlerResult.succeeded(outputs={"hidden": 1}),
    ):
        result = services.execution.start(plan, origin="test").wait(3)
    assert result.outputs == {} and result.outputs_complete


def test_optional_last_missing_does_not_reuse_an_earlier_measurement(services):
    node = item(services, output=NodeOutput("tube", "/height", "last", False))
    plan = ExecutionPlan.from_entries([LoopBlock("loop", [node], 2)])
    with patch.object(
        services.execution._manager._engine._handler_registry,
        "execute",
        side_effect=[
            ActionHandlerResult.succeeded(outputs={"height": 23.6}),
            ActionHandlerResult.succeeded(),
        ],
    ):
        result = services.execution.start(plan, origin="test").wait(3)
    assert result.outputs == {} and result.outputs_complete


def test_vision_pipeline_to_saved_task_and_durable_query(services):
    services.devices.initialize("robot-system")
    services.devices.initialize("camera")
    workflow = document(
        services,
        [
            item(
                services,
                output=NodeOutput("tube", "/height"),
                action_type=ActionType.VISION_RELOCALIZE,
            )
        ],
    )
    services.composition.save_workflow("measure", workflow, origin="test")
    with patch.object(
        services.vision,
        "_relocalization_pipeline",
        return_value=VisionPipelineResult(
            True, 1, 1, outputs={"height": 23.6, "unit": "mm", "detected": True}
        ),
    ):
        receipt = services.saved_tasks.run_task(
            "measure", TaskRequestContext("tester", "one", "test")
        )
        snapshot = services.execution.wait(3)
    assert snapshot.outputs == {"tube": 23.6}, snapshot.error
    result = services.saved_tasks.get_run(receipt["run_id"], "tester")
    assert result["result"]["outputs"] == {"tube": 23.6}
    assert result["result"]["outputs_complete"]
    path = services.saved_tasks._journal._path
    reopened = TaskRunJournal(path).find_run("tester", receipt["run_id"])
    assert reopened["result"]["outputs"] == {"tube": 23.6}


def test_invalid_vision_outputs_are_not_reported_as_device_or_config_errors(services):
    services.devices.initialize("robot-system")
    services.devices.initialize("camera")
    workflow = document(
        services,
        [item(services, output=NodeOutput("tube"), action_type=ActionType.VISION_RELOCALIZE)],
    )

    def invalid(*_args):
        return VisionPipelineResult(True, 1, 1, outputs={"height": float("nan")})

    with patch.object(services.vision, "_relocalization_pipeline", side_effect=invalid):
        result = services.execution.start(
            services.workflow_compiler.compile(workflow).plan, origin="test"
        ).wait(3)
    assert result.error_code == "invalid_output"


def test_cancelled_run_retains_completed_exports(services):
    plan = ExecutionPlan.from_entries(
        [item(services, "first", NodeOutput("first")), item(services, "second")]
    )

    def handler(_type, parameters, context):
        if context.action_name == "first":
            return context.success(outputs={"height": 23.6})
        context.control.cancel()
        context.checkpoint()

    with patch.object(
        services.execution._manager._engine._handler_registry, "execute", side_effect=handler
    ):
        result = services.execution.start(plan, origin="test").wait(3)
    assert result.state.value == "cancelled"
    assert result.outputs == {"first": {"height": 23.6}} and not result.outputs_complete


def test_run_export_budget_rejects_new_value_without_destroying_previous():
    collector = OutputCollector()
    for index in range(8):
        collector.add(
            NodeOutput(f"value_{index}"),
            {"text": "x" * 65000},
            ExecutionStepIdentity(index, str(index), str(index), str(index)),
        )
    with pytest.raises(ValueError, match="字节"):
        collector.add(
            NodeOutput("overflow"), {"text": "x" * 65000}, ExecutionStepIdentity(9, "9", "9", "9")
        )
    assert len(collector.snapshot()) == 8


def test_tcp_final_event_and_status_query_return_same_business_outputs(services):
    services.devices.initialize("robot-system")
    services.devices.initialize("camera")
    workflow = document(
        services,
        [item(services, output=NodeOutput("tube"), action_type=ActionType.VISION_RELOCALIZE)],
    )
    services.composition.save_workflow("measure", workflow, origin="test")

    async def scenario():
        access = ServerAccessController("", security_enabled=False, control_lease_seconds=30)
        server = RobotTcpServer(
            TaskApi(services.saved_tasks, access),
            replace(services.settings.server, tcp_port=0),
            authentication_configured=False,
        )
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        async def send(command, request_id, **values):
            writer.write(
                (
                    json.dumps(
                        {"version": 1, "request_id": request_id, "command": command, **values}
                    )
                    + "\n"
                ).encode()
            )
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), 3))

        try:
            assert (await send("control.acquire", "control"))["ok"]
            with patch.object(
                services.vision,
                "_relocalization_pipeline",
                return_value=VisionPipelineResult(
                    True, 1, 1, outputs={"height": 23.6, "unit": "mm"}
                ),
            ):
                accepted = await send("task.run", "measure-one", task="measure")
                assert accepted["status"] == "accepted"
                while True:
                    finished = json.loads(await asyncio.wait_for(reader.readline(), 3))
                    if finished["event"] == "run.finished":
                        break
            assert finished["result"]["outputs"]["tube"]["height"] == 23.6
            assert finished["result"]["outputs"]["tube"]["unit"] == "mm"
            assert finished["result"]["outputs"]["tube"]["frames_processed"] == 1
            assert finished["result"]["outputs_complete"]
            queried = await send("run.status", "query", run_id=accepted["run_id"])
            assert queried["result"] == finished["result"]
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()

    asyncio.run(scenario())


def test_websocket_final_response_contains_exports():
    server = RobotWebSocketServer(services=object())
    messages = []
    server._broadcast_threadsafe = messages.append
    from src.execution import ExecutionEvent

    server._execution_handler._on_execution_event(
        ExecutionEvent(
            "run",
            ExecutionEventType.SUCCEEDED,
            "test",
            data={"outputs": {"height": 23.6}, "outputs_complete": True},
        )
    )
    assert messages[-1]["outputs"] == {"height": 23.6}
    assert messages[-1]["outputs_complete"]


def test_unauthenticated_websocket_observer_does_not_receive_measurements():
    async def scenario():
        server = RobotWebSocketServer(services=object(), auth_token="key", security_enabled=True)
        public, authenticated = object(), object()
        server._register_client(public, "local")
        identity = server._register_client(authenticated, "local")
        server._access.authenticate(identity, "key")
        deliveries = {}

        async def deliver(payload, clients):
            for client in clients:
                deliveries[client] = payload
            return []

        server._deliver = deliver
        await server._broadcast(
            {"event": "execution_finished", "outputs": {"height": 23.6}, "outputs_complete": True}
        )
        assert deliveries[authenticated]["outputs"] == {"height": 23.6}
        assert deliveries[public]["outputs"] == {}
        assert not deliveries[public]["outputs_complete"]

    asyncio.run(scenario())
