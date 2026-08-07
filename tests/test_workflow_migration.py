from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.bootstrap.workflow_cli import _apply, _plan
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.workflow import WorkflowDocument
from src.persistence.json_documents import read_json_document


class WorkflowMigrationTests(unittest.TestCase):
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
