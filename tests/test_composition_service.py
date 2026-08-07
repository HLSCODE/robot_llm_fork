from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from src.application import (
    CompositionChangeType,
    CompositionRevisionConflict,
    CompositionService,
)
from src.domain.models import (
    ActionDefinition,
    ActionType,
    SequenceItem,
)
from src.persistence.storage import JsonCompositionRepository
from src.robot_server.ws_server import RobotWebSocketServer


class CompositionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        root = Path(self._temporary_directory.name)
        self.repository = JsonCompositionRepository(
            actions_directory=root / "actions",
            tasks_directory=root / "tasks",
        )
        self.service = CompositionService(self.repository)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_actions_and_sequence_are_returned_as_defensive_copies(self):
        events = []
        self.service.subscribe(events.append)
        created = self.service.create_action(
            _action("action-1", nested_value=1),
            origin="test",
        )

        created.parameters["补偿"]["value"] = 99
        stored = self.service.get_action("action-1")

        self.assertEqual(1, stored.parameters["补偿"]["value"])
        self.service.append_action_ids(
            ("action-1",),
            origin="test",
        )
        sequence = self.service.sequence_entries()
        sequence[0].definition.name = "mutated"

        self.assertEqual(
            "Action action-1",
            self.service.sequence_entries()[0].definition.name,
        )
        self.assertEqual(
            [
                CompositionChangeType.ACTIONS,
                CompositionChangeType.SEQUENCE,
            ],
            [event.change_type for event in events],
        )
        self.assertEqual([1, 2], [event.revision for event in events])
        self.assertEqual(
            [1, 1],
            [event.change_revision for event in events],
        )

    def test_stale_sequence_replacement_cannot_overwrite_newer_change(self):
        first = _sequence_item("first")
        second = _sequence_item("second")
        displayed_revision = self.service.sequence_revision
        self.service.replace_sequence(
            (first,),
            origin="websocket",
        )

        with self.assertRaises(CompositionRevisionConflict):
            self.service.replace_sequence(
                (second,),
                origin="gui",
                expected_revision=displayed_revision,
            )

        self.assertEqual(
            "first",
            self.service.sequence_entries()[0].definition.id,
        )

    def test_concurrent_action_creation_does_not_lose_updates(self):
        action_count = 40

        def create(index: int) -> None:
            self.service.create_action(
                _action(f"action-{index}"),
                origin=f"worker-{index}",
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(create, range(action_count)))

        stored_ids = {
            action.id
            for action in self.repository.load_actions()
        }
        self.assertEqual(action_count, len(stored_ids))
        self.assertEqual(
            {f"action-{index}" for index in range(action_count)},
            stored_ids,
        )
        temporary_files = tuple(
            self.repository.tasks_directory.parent.glob("*.tmp")
        )
        self.assertEqual((), temporary_files)

    def test_task_mutations_are_serialized_and_publish_events(self):
        self.service.create_action(
            _action("first"),
            origin="test",
        )
        self.service.create_action(
            _action("second"),
            origin="test",
        )
        self.service.append_action_ids(
            ("first", "second"),
            origin="test",
        )
        events = []
        self.service.subscribe(events.append)

        stored_name = self.service.save_current_task(
            "workflow",
            origin="test",
        )
        removed, remaining = self.service.remove_task_entry(
            stored_name,
            0,
            origin="test",
        )

        self.assertEqual("workflow.task", stored_name)
        self.assertEqual("first", removed.definition.id)
        self.assertEqual("second", remaining[0].definition.id)
        summaries = self.service.list_tasks()
        self.assertEqual(
            [("workflow.task", 1)],
            [(summary.name, summary.step_count) for summary in summaries],
        )
        self.assertEqual(
            [
                CompositionChangeType.TASKS,
                CompositionChangeType.TASKS,
            ],
            [event.change_type for event in events],
        )

    def test_missing_entities_fail_without_partial_mutation(self):
        self.service.create_action(
            _action("available"),
            origin="test",
        )

        with self.assertRaises(KeyError):
            self.service.append_action_ids(
                ("available", "missing"),
                origin="test",
            )
        with self.assertRaises(FileNotFoundError):
            self.service.load_task("missing.task")

        self.assertEqual((), self.service.sequence_entries())

    def test_failed_json_write_preserves_last_complete_document(self):
        valid = _action("valid")
        self.repository.save_actions((valid,))
        invalid = _action("invalid")
        invalid.parameters["unsupported"] = object()

        with self.assertRaises(TypeError):
            self.repository.save_actions((invalid,))

        self.assertEqual(
            ["valid"],
            [
                action.id
                for action in self.repository.load_actions()
            ],
        )


class CompositionWebSocketTests(unittest.TestCase):
    def test_websocket_handlers_use_shared_composition_service(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            composition = CompositionService(
                JsonCompositionRepository(
                    actions_directory=root / "actions",
                    tasks_directory=root / "tasks",
                )
            )
            server = RobotWebSocketServer(
                services=SimpleNamespace(composition=composition),
            )
            client = _FakeWebSocket()

            async def scenario() -> None:
                await server._composition_handler._handle_create_action(
                    client,
                    {
                        "name": "Wait",
                        "type": ActionType.WAIT.value,
                        "parameters": {"duration": 0},
                    },
                )
                action_id = composition.list_actions()[0].id
                await server._composition_handler._handle_add_to_sequence(
                    client,
                    {"action_ids": [action_id]},
                )
                await server._composition_handler._handle_save_task(
                    client,
                    {"name": "shared"},
                )

            asyncio.run(scenario())

            events = [
                json.loads(message)["event"]
                for message in client.messages
            ]
            self.assertEqual(
                [
                    "action_created",
                    "sequence_updated",
                    "task_saved",
                ],
                events,
            )
            self.assertEqual(
                1,
                len(composition.sequence_entries()),
            )
            self.assertEqual(
                ("shared.task",),
                tuple(
                    summary.name
                    for summary in composition.list_tasks()
                ),
            )

    def test_gui_originated_change_is_broadcast_to_websocket_clients(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            composition = CompositionService(
                JsonCompositionRepository(
                    actions_directory=root / "actions",
                    tasks_directory=root / "tasks",
                )
            )
            server = RobotWebSocketServer(
                services=SimpleNamespace(composition=composition),
            )
            client = _FakeWebSocket()

            async def scenario() -> None:
                server._loop = asyncio.get_running_loop()
                server._clients.add(client)
                unsubscribe = composition.subscribe(
                    server._on_composition_event
                )
                try:
                    composition.create_action(
                        _action("gui-action"),
                        origin="gui",
                    )
                    for _ in range(20):
                        if client.messages:
                            break
                        await asyncio.sleep(0.01)
                finally:
                    unsubscribe()
                    await server._cancel_background_tasks()
                    server._loop = None

            asyncio.run(scenario())

            payload = json.loads(client.messages[0])
            self.assertEqual("composition_changed", payload["event"])
            self.assertEqual("actions", payload["change"])
            self.assertEqual("gui", payload["origin"])
            self.assertEqual(
                "gui-action",
                payload["actions"][0]["id"],
            )


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def _action(
    action_id: str,
    *,
    nested_value: int = 0,
) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        name=f"Action {action_id}",
        type=ActionType.MOVE,
        parameters={
            "目标": "机械臂",
            "臂": "左",
            "模式": "move_j",
            "点位": [0, 0, 0, 0, 0, 0],
            "补偿": {"value": nested_value},
        },
    )


def _sequence_item(action_id: str) -> SequenceItem:
    return SequenceItem.from_definition(_action(action_id))


if __name__ == "__main__":
    unittest.main()
