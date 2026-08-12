from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.bootstrap.workflow_cli import (
    _apply,
    _plan,
    migrate_legacy_workflows,
    normalize_active_workflows,
)
from src.bootstrap.launcher import migrate_startup_workflows
from src.configuration.settings import ApplicationSettings
from src.domain.models import ActionDefinition, ActionType, LoopBlock, SequenceItem
from src.domain.workflow import WorkflowDocument
from src.persistence.json_documents import read_json_document


class WorkflowMigrationTests(unittest.TestCase):
    def test_startup_migration_discovers_tasks_below_data_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            workflows = root / "configured-workflows"
            drafts = root / "configured-drafts"
            tasks.mkdir()
            item = SequenceItem(
                uuid="step-1",
                definition=ActionDefinition(
                    id="wait",
                    name="Wait",
                    type=ActionType.WAIT,
                    parameters={"wait_seconds": 1.0},
                ),
            )
            (tasks / "legacy.task").write_text(
                json.dumps([item.to_dict()]),
                encoding="utf-8",
            )

            defaults = ApplicationSettings.defaults()
            settings = replace(
                defaults,
                data=replace(
                    defaults.data,
                    robot_data_dir=str(root),
                    workflows_directory=str(workflows),
                    workflow_drafts_directory=str(drafts),
                ),
            )
            result = migrate_startup_workflows(settings)

            target = workflows / "legacy.workflow.json"
            self.assertEqual(1, result.migrated_count)
            self.assertEqual((target,), result.migrated_files)
            self.assertTrue(target.is_file())
            self.assertTrue(
                (result.backup_directory / "legacy.task").is_file()
            )
            self.assertEqual(
                0,
                migrate_legacy_workflows(
                    root,
                    workflows_directory=workflows,
                    drafts_directory=drafts,
                ).migrated_count,
            )

    def test_active_workflow_text_poses_are_normalized_recursively(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflows = root / "workflows"
            backups = root / "migration-backups" / "workflow-pose-v1"
            workflows.mkdir()
            move = SequenceItem(
                uuid="move-1",
                definition=ActionDefinition(
                    id="",
                    name="Move",
                    type=ActionType.MOVE,
                    parameters={
                        "目标": "机械臂移动到指定点位",
                        "臂": "左",
                        "模式": "move_j",
                        "点位": "[200~[1, 2, 3, 4, 5, 6]",
                    },
                ),
            )
            document = WorkflowDocument.from_entries(
                workflow_id="demo",
                name="demo",
                revision=1,
                entries=(
                    LoopBlock(
                        uuid="loop-1",
                        items=[
                            move,
                            SequenceItem.from_dict(move.to_dict()),
                        ],
                        repeat_count=2,
                    ),
                ),
            )
            path = workflows / "demo.workflow.json"
            path.write_text(
                json.dumps(document.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(
                1,
                normalize_active_workflows(workflows, backups),
            )

            restored = WorkflowDocument.from_dict(read_json_document(path))
            loop = restored.to_entries()[0]
            self.assertIsInstance(loop, LoopBlock)
            assert isinstance(loop, LoopBlock)
            item = loop.items[0]
            self.assertIsInstance(item, SequenceItem)
            assert isinstance(item, SequenceItem)
            self.assertEqual(
                [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                item.definition.parameters["点位"],
            )
            self.assertTrue(item.definition.id.startswith("legacy-"))
            duplicate = loop.items[1]
            assert isinstance(duplicate, SequenceItem)
            self.assertNotEqual(item.uuid, duplicate.uuid)
            self.assertTrue((backups / path.name).is_file())

    def test_legacy_task_is_staged_verified_and_archived(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            workflows = root / "workflows"
            drafts = root / "drafts"
            backup = root / "migration-backups" / "workflow-v1"
            tasks.mkdir()
            item = SequenceItem(
                uuid="step-1",
                definition=ActionDefinition(
                    id="wait",
                    name="Wait",
                    type=ActionType.WAIT,
                    parameters={"wait_seconds": 1.0},
                ),
            )
            source = tasks / "demo.task"
            source.write_text(
                json.dumps([item.to_dict()]),
                encoding="utf-8",
            )
            (tasks / "demo.task.v0.bak").write_text("backup", encoding="utf-8")

            items = _plan(tasks, workflows, drafts)
            _apply(items, tasks, workflows, backup)

            target = workflows / "demo.workflow.json"
            document = WorkflowDocument.from_dict(read_json_document(target))
            self.assertEqual("demo", document.workflow_id)
            self.assertEqual(("step-1",), tuple(entry.uuid for entry in document.to_entries()))
            self.assertFalse(source.exists())
            self.assertTrue((backup / "demo.task").is_file())
            self.assertTrue((backup / "demo.task.v0.bak").is_file())

    def test_existing_target_aborts_before_any_write(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            workflows = root / "workflows"
            tasks.mkdir()
            workflows.mkdir()
            (tasks / "demo.task").write_text("[]", encoding="utf-8")
            target = workflows / "demo.workflow.json"
            target.write_text("{}", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                _plan(tasks, workflows, root / "drafts")

            self.assertEqual("{}", target.read_text(encoding="utf-8"))
            self.assertTrue((tasks / "demo.task").is_file())


if __name__ == "__main__":
    unittest.main()
