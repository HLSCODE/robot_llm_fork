from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.application.builtin_data import BuiltinDataInstaller
from src.configuration.data_paths import ApplicationDataPaths
from src.persistence.json_documents import (
    JsonDocumentSchemaError,
    UnsupportedJsonDocumentVersion,
)
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.persistence.storage import JsonCompositionRepository
from src.persistence.cli import run_data_operation
from src.skill_system.default_skills import get_default_skills
from src.skill_system.skill_registry import SkillRegistry


class CompositionDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.actions_file = self.root / "actions.json"
        self.tasks_directory = self.root / "tasks"
        self.repository = JsonCompositionRepository(
            actions_file=self.actions_file,
            tasks_directory=self.tasks_directory,
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_new_actions_and_tasks_use_explicit_schema_version(self) -> None:
        action = _action()
        self.repository.save_actions((action,))
        task_name = self.repository.save_task(
            "demo",
            (SequenceItem.from_definition(action),),
        )

        actions_document = _read_json(self.actions_file)
        task_document = _read_json(self.tasks_directory / task_name)

        self.assertEqual("robot_llm.actions", actions_document["schema"])
        self.assertEqual(1, actions_document["schema_version"])
        self.assertEqual([action.id], [item["id"] for item in actions_document["actions"]])
        self.assertEqual("robot_llm.task", task_document["schema"])
        self.assertEqual(1, task_document["schema_version"])
        self.assertEqual(1, len(task_document["entries"]))

    def test_legacy_document_reads_do_not_mutate_source_files(self) -> None:
        action = _action()
        legacy_actions = [action.to_dict()]
        self.actions_file.write_text(
            json.dumps(legacy_actions, ensure_ascii=False),
            encoding="utf-8",
        )
        task_path = self.tasks_directory / "legacy.task"
        task_path.parent.mkdir(parents=True)
        legacy_task = [SequenceItem.from_definition(action).to_dict()]
        task_path.write_text(
            json.dumps(legacy_task, ensure_ascii=False),
            encoding="utf-8",
        )

        loaded_actions = self.repository.load_actions()
        loaded_task = self.repository.load_task("legacy")

        self.assertEqual([action.id], [item.id for item in loaded_actions])
        self.assertEqual(1, len(loaded_task or ()))
        self.assertEqual(legacy_actions, _read_json(self.actions_file))
        self.assertEqual(legacy_task, _read_json(task_path))
        self.assertFalse(self.actions_file.with_name("actions.json.v0.bak").exists())
        self.assertFalse(task_path.with_name("legacy.task.v0.bak").exists())

    def test_explicit_legacy_migration_preserves_exact_backups(self) -> None:
        action = _action()
        legacy_actions = [action.to_dict()]
        self.actions_file.write_text(
            json.dumps(legacy_actions, ensure_ascii=False),
            encoding="utf-8",
        )
        task_path = self.tasks_directory / "legacy.task"
        task_path.parent.mkdir(parents=True)
        legacy_task = [SequenceItem.from_definition(action).to_dict()]
        task_path.write_text(
            json.dumps(legacy_task, ensure_ascii=False),
            encoding="utf-8",
        )

        self.assertTrue(self.repository.migrate_legacy_actions())
        self.assertEqual(("legacy.task",), self.repository.migrate_legacy_tasks())

        self.assertEqual(
            legacy_actions,
            _read_json(self.actions_file.with_name("actions.json.v0.bak")),
        )
        self.assertEqual(
            legacy_task,
            _read_json(task_path.with_name("legacy.task.v0.bak")),
        )
        self.assertEqual(1, _read_json(self.actions_file)["schema_version"])
        self.assertEqual(1, _read_json(task_path)["schema_version"])

    def test_future_schema_version_is_rejected_without_rewrite(self) -> None:
        future_document = {
            "schema": "robot_llm.actions",
            "schema_version": 99,
            "actions": [],
        }
        self.actions_file.write_text(
            json.dumps(future_document),
            encoding="utf-8",
        )

        with self.assertRaises(UnsupportedJsonDocumentVersion):
            self.repository.load_actions()

        self.assertEqual(future_document, _read_json(self.actions_file))
        self.assertFalse(self.actions_file.with_name("actions.json.v0.bak").exists())

    def test_invalid_action_fails_with_document_context(self) -> None:
        self.actions_file.write_text(
            json.dumps(
                {
                    "schema": "robot_llm.actions",
                    "schema_version": 1,
                    "actions": [{"name": "missing id"}],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            JsonDocumentSchemaError,
            "stable id",
        ):
            self.repository.load_actions()

    def test_task_name_must_be_a_plain_file_name(self) -> None:
        with self.assertRaises(ValueError):
            self.repository.load_task("../outside")
        with self.assertRaises(ValueError):
            self.repository.save_task("nested/task", ())


class BuiltinDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        root = Path(self._temporary_directory.name)
        self.paths = ApplicationDataPaths(
            root=root,
            actions_file=root / "actions.json",
            tasks_directory=root / "tasks",
            skills_file=root / "skills.json",
        )
        SkillRegistry().reset()

    def tearDown(self) -> None:
        SkillRegistry().reset()
        self._temporary_directory.cleanup()

    def test_missing_user_catalogs_are_seeded_without_future_overwrite(self) -> None:
        installer = BuiltinDataInstaller(self.paths)

        first = installer.install_missing()
        original_actions = self.paths.actions_file.read_bytes()
        second = installer.install_missing()

        self.assertEqual(
            {self.paths.actions_file, self.paths.skills_file},
            set(first.created_files),
        )
        self.assertEqual((), second.created_files)
        self.assertEqual(original_actions, self.paths.actions_file.read_bytes())
        actions = JsonCompositionRepository(
            actions_file=self.paths.actions_file,
            tasks_directory=self.paths.tasks_directory,
        ).load_actions()
        self.assertEqual(
            ["builtin.wait.1s", "builtin.wait.3s"],
            [action.id for action in actions],
        )
        registry = SkillRegistry()
        self.assertEqual(
            len(get_default_skills()),
            registry.load_from_json(self.paths.skills_file),
        )

    def test_legacy_skill_read_does_not_mutate_source_file(self) -> None:
        skill = get_default_skills()[0]
        legacy_document = {"skills": [skill.to_dict()]}
        self.paths.skills_file.write_text(
            json.dumps(legacy_document, ensure_ascii=False),
            encoding="utf-8",
        )

        count = SkillRegistry().load_from_json(self.paths.skills_file)

        self.assertEqual(1, count)
        self.assertEqual(legacy_document, _read_json(self.paths.skills_file))
        self.assertFalse(
            self.paths.skills_file.with_name("skills.json.v0.bak").exists()
        )

    def test_explicit_skill_migration_keeps_backup(self) -> None:
        skill = get_default_skills()[0]
        legacy_document = {"skills": [skill.to_dict()]}
        self.paths.skills_file.write_text(
            json.dumps(legacy_document, ensure_ascii=False),
            encoding="utf-8",
        )

        self.assertTrue(SkillRegistry().migrate_json(self.paths.skills_file))

        self.assertEqual(
            legacy_document,
            _read_json(self.paths.skills_file.with_name("skills.json.v0.bak")),
        )
        migrated = _read_json(self.paths.skills_file)
        self.assertEqual("robot_llm.skills", migrated["schema"])
        self.assertEqual(1, migrated["schema_version"])

    def test_legacy_skill_defaults_are_normalized_during_migration(self) -> None:
        legacy_skill = get_default_skills()[0].to_dict()
        legacy_skill["parameters"] = [
            {
                "name": "volume",
                "param_label": "Volume",
                "type": "int",
                "default": 1,
            }
        ]
        legacy_skill["steps"][0].pop("parameter_bindings")
        legacy_document = {"skills": [legacy_skill]}
        self.paths.skills_file.write_text(
            json.dumps(legacy_document, ensure_ascii=False),
            encoding="utf-8",
        )

        self.assertTrue(SkillRegistry().migrate_json(self.paths.skills_file))

        migrated_skill = _read_json(self.paths.skills_file)["skills"][0]
        self.assertEqual("", migrated_skill["parameters"][0]["unit"])
        self.assertEqual({}, migrated_skill["steps"][0]["parameter_bindings"])

    def test_invalid_legacy_skill_is_not_committed_as_current_schema(self) -> None:
        legacy_document = {"skills": [{"id": "broken"}]}
        original = json.dumps(legacy_document).encode()
        self.paths.skills_file.write_bytes(original)

        with self.assertRaises(JsonDocumentSchemaError):
            SkillRegistry().load_from_json(self.paths.skills_file)

        self.assertEqual(original, self.paths.skills_file.read_bytes())
        self.assertFalse(self.paths.skills_file.with_name("skills.json.v0.bak").exists())

    def test_corrupt_skill_is_not_silently_replaced_by_defaults(self) -> None:
        self.paths.skills_file.write_text(
            json.dumps(
                {
                    "schema": "robot_llm.skills",
                    "schema_version": 1,
                    "skills": [{"id": "broken"}],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(JsonDocumentSchemaError):
            SkillRegistry().load_from_json(self.paths.skills_file)

        self.assertEqual([], SkillRegistry().list_skills())

    def test_validate_operation_is_read_only_and_migrate_is_explicit(self) -> None:
        action = _action()
        legacy_actions = json.dumps([action.to_dict()], ensure_ascii=False)
        legacy_task = json.dumps(
            [SequenceItem.from_definition(action).to_dict()],
            ensure_ascii=False,
        )
        skill = get_default_skills()[0]
        legacy_skills = json.dumps(
            {"skills": [skill.to_dict()]},
            ensure_ascii=False,
        )
        self.paths.actions_file.write_text(legacy_actions, encoding="utf-8")
        self.paths.tasks_directory.mkdir(parents=True)
        (self.paths.tasks_directory / "demo.task").write_text(
            legacy_task,
            encoding="utf-8",
        )
        self.paths.skills_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.skills_file.write_text(legacy_skills, encoding="utf-8")

        validated = run_data_operation(
            actions_file=self.paths.actions_file,
            tasks_directory=self.paths.tasks_directory,
            skills_file=self.paths.skills_file,
            migrate=False,
        )

        self.assertEqual((1, 1, 1), (
            validated.action_count,
            validated.task_count,
            validated.skill_count,
        ))
        self.assertEqual(legacy_actions, self.paths.actions_file.read_text(encoding="utf-8"))
        migrated = run_data_operation(
            actions_file=self.paths.actions_file,
            tasks_directory=self.paths.tasks_directory,
            skills_file=self.paths.skills_file,
            migrate=True,
        )
        self.assertTrue(migrated.migrated_actions)
        self.assertEqual(("demo.task",), migrated.migrated_tasks)
        self.assertTrue(migrated.migrated_skills)


def _action() -> ActionDefinition:
    return ActionDefinition(
        id="action-1",
        name="Wait",
        type=ActionType.WAIT,
        parameters={"wait_seconds": 1.0},
    )


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
