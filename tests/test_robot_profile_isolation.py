from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.application.composition import CompositionService
from src.application.robot_profile_migration import LegacyRobotProfileMigrator
from src.application.factory import create_application_services
from src.application.workflow_editing import WorkflowEditingSession
from src.application.workflow_compiler import WorkflowCompilationError, WorkflowCompiler
from src.configuration.data_paths import ApplicationDataPaths
from src.configuration.settings import ApplicationSettings, DataSettings
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.workflow import WorkflowDocument
from src.persistence.storage import JsonCompositionRepository


REALMAN_PROFILE = "realman-rm75-dual"
TIANJI_PROFILE = "tianji-tianji-dual"


class RobotProfileIsolationTests(unittest.TestCase):
    def test_default_data_directories_are_isolated_by_profile(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            settings = DataSettings(robot_data_dir=temporary_directory)
            realman = ApplicationDataPaths.from_settings(settings, REALMAN_PROFILE)
            tianji = ApplicationDataPaths.from_settings(settings, TIANJI_PROFILE)

        self.assertNotEqual(realman.profile_root, tianji.profile_root)
        self.assertTrue(realman.actions_directory.is_relative_to(realman.profile_root))
        self.assertTrue(tianji.workflows_directory.is_relative_to(tianji.profile_root))

    def test_repository_rejects_action_from_another_profile(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = JsonCompositionRepository(
                robot_profile_id=REALMAN_PROFILE,
                actions_directory=root / "actions",
                workflows_directory=root / "workflows",
                workflow_drafts_directory=root / "drafts",
            )
            action = _wait_action(TIANJI_PROFILE)

            with self.assertRaisesRegex(ValueError, "active profile"):
                repository.save_actions((action,))

    def test_workflow_compiler_rejects_another_profile(self) -> None:
        document = WorkflowDocument.from_entries(
            workflow_id="foreign",
            name="foreign",
            revision=1,
            entries=(SequenceItem.from_definition(_wait_action(TIANJI_PROFILE)),),
            robot_profile_id=TIANJI_PROFILE,
        )

        with self.assertRaises(WorkflowCompilationError):
            WorkflowCompiler(
                expected_robot_profile_id=REALMAN_PROFILE
            ).compile(document)

    def test_execution_runtime_rejects_another_profile_before_dispatch(self) -> None:
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        item = SequenceItem.from_definition(_wait_action(TIANJI_PROFILE))

        with self.assertRaisesRegex(ValueError, "active profile"):
            services.execution.start_entries((item,), origin="profile-test")

    def test_editor_scopes_unscoped_actions_to_the_active_profile(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = JsonCompositionRepository(
                robot_profile_id=TIANJI_PROFILE,
                actions_directory=root / "actions",
                workflows_directory=root / "workflows",
                workflow_drafts_directory=root / "drafts",
            )
            editing = WorkflowEditingSession(CompositionService(repository))
            document = WorkflowDocument.from_entries(
                workflow_id="edited",
                name="edited",
                revision=0,
                entries=(SequenceItem.from_definition(_wait_action("unscoped")),),
                robot_profile_id=TIANJI_PROFILE,
            )

            state = editing.replace_document(document)

            action = state.document.to_entries()[0]
            assert isinstance(action, SequenceItem)
            self.assertEqual(TIANJI_PROFILE, action.definition.robot_profile_id)

    def test_editor_rejects_actions_from_another_profile(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = JsonCompositionRepository(
                robot_profile_id=TIANJI_PROFILE,
                actions_directory=root / "actions",
                workflows_directory=root / "workflows",
                workflow_drafts_directory=root / "drafts",
            )
            editing = WorkflowEditingSession(CompositionService(repository))
            document = WorkflowDocument.from_entries(
                workflow_id="foreign",
                name="foreign",
                revision=0,
                entries=(SequenceItem.from_definition(_wait_action(REALMAN_PROFILE)),),
                robot_profile_id=TIANJI_PROFILE,
            )

            with self.assertRaisesRegex(ValueError, "another Robot Profile"):
                editing.replace_document(document)

    def test_legacy_realman_data_is_copied_and_stamped_once(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = ApplicationDataPaths.from_settings(
                DataSettings(robot_data_dir=str(root)),
                REALMAN_PROFILE,
            )
            legacy_actions = root / "actions" / "library.json"
            legacy_actions.parent.mkdir(parents=True)
            legacy_actions.write_text(
                json.dumps(
                    {
                        "schema": "robot_llm.actions",
                        "schema_version": 2,
                        "actions": [_wait_action("unscoped").to_dict()],
                    }
                ),
                encoding="utf-8",
            )
            legacy_workflows = root / "workflows"
            legacy_workflows.mkdir()
            raw_workflow = WorkflowDocument.from_entries(
                workflow_id="legacy",
                name="legacy",
                revision=1,
                entries=(
                    SequenceItem.from_definition(_wait_action("unscoped")),
                ),
            ).to_dict()
            raw_workflow["schema_version"] = 4
            raw_workflow.pop("robot_profile_id")
            raw_workflow["root"]["children"][0]["definition"].pop(
                "robot_profile_id"
            )
            (legacy_workflows / "legacy.workflow.json").write_text(
                json.dumps(raw_workflow),
                encoding="utf-8",
            )

            migrator = LegacyRobotProfileMigrator(paths, provider="realman")
            first = migrator.migrate_missing()
            second = migrator.migrate_missing()
            repository = JsonCompositionRepository(
                robot_profile_id=REALMAN_PROFILE,
                actions_directory=paths.actions_directory,
                workflows_directory=paths.workflows_directory,
                workflow_drafts_directory=paths.workflow_drafts_directory,
            )

            self.assertEqual(2, len(first.migrated_files))
            self.assertEqual((), second.migrated_files)
            self.assertEqual(REALMAN_PROFILE, repository.load_actions()[0].robot_profile_id)
            restored = repository.load_workflow("legacy")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(REALMAN_PROFILE, restored.robot_profile_id)
            self.assertTrue(legacy_actions.is_file())

    def test_legacy_data_is_not_assigned_to_tianji(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "actions").mkdir()
            (root / "actions" / "library.json").write_text("[]", encoding="utf-8")
            paths = ApplicationDataPaths.from_settings(
                DataSettings(robot_data_dir=str(root)),
                TIANJI_PROFILE,
            )

            result = LegacyRobotProfileMigrator(
                paths,
                provider="tianji",
            ).migrate_missing()

            self.assertEqual((), result.migrated_files)
            self.assertFalse((paths.actions_directory / "library.json").exists())

    def test_legacy_realman_text_pose_is_normalized_during_migration(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = ApplicationDataPaths.from_settings(
                DataSettings(robot_data_dir=str(root)),
                REALMAN_PROFILE,
            )
            legacy_actions = root / "actions" / "library.json"
            legacy_actions.parent.mkdir(parents=True)
            legacy_actions.write_text(
                json.dumps(
                    {
                        "schema": "robot_llm.actions",
                        "schema_version": 2,
                        "actions": [_move_action("unscoped").to_dict()],
                    }
                ),
                encoding="utf-8",
            )
            migrator = LegacyRobotProfileMigrator(paths, provider="realman")

            migrator.migrate_missing()

            repository = JsonCompositionRepository(
                robot_profile_id=REALMAN_PROFILE,
                actions_directory=paths.actions_directory,
                workflows_directory=paths.workflows_directory,
                workflow_drafts_directory=paths.workflow_drafts_directory,
            )
            self.assertEqual(
                [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                repository.load_actions()[0].parameters["点位"],
            )

            target = paths.actions_directory / "library.json"
            migrated_document = json.loads(target.read_text(encoding="utf-8"))
            migrated_document["actions"][0]["parameters"]["点位"] = (
                "pose = [6, 5, 4, 3, 2, 1]"
            )
            target.write_text(json.dumps(migrated_document), encoding="utf-8")

            repaired = migrator.migrate_missing()

            self.assertEqual((target,), repaired.migrated_files)
            self.assertEqual(
                [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
                repository.load_actions()[0].parameters["点位"],
            )


def _wait_action(robot_profile_id: str) -> ActionDefinition:
    return ActionDefinition(
        id="wait",
        name="wait",
        type=ActionType.WAIT,
        parameters={"wait_seconds": 1.0},
        robot_profile_id=robot_profile_id,
    )


def _move_action(robot_profile_id: str) -> ActionDefinition:
    return ActionDefinition(
        id="1",
        name="legacy move",
        type=ActionType.MOVE,
        parameters={
            "目标": "机械臂",
            "臂": "左",
            "模式": "move_j",
            "点位": "pose = [1, 2, 3, 4, 5, 6]",
            "补偿": {"mode": "none"},
        },
        robot_profile_id=robot_profile_id,
    )


if __name__ == "__main__":
    unittest.main()
