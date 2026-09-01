from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.application.builtin_data import BuiltinDataInstaller
from src.configuration.data_paths import ApplicationDataPaths
from src.domain.models import ActionDefinition, ActionType
from src.bootstrap.catalog_cli import (
    migrate_catalogs,
    normalize_action_catalog,
    validate_catalogs,
)
from src.persistence.json_documents import (
    JsonDocumentSchemaError,
    UnsupportedJsonDocumentVersion,
    write_single_document,
)
from src.persistence.storage import JsonCompositionRepository
from src.skill_system.models import Skill, SkillCategory, SkillStep
from src.skill_system.skill_registry import SKILL_DOCUMENT, SkillRegistry


class ActionCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.actions_directory = self.root / "actions"
        self.repository = JsonCompositionRepository(
            actions_directory=self.actions_directory,
            workflows_directory=self.root / "workflows",
            workflow_drafts_directory=self.root / "drafts",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_save_and_load_use_schema_v3_profiled_catalog(self) -> None:
        self.repository.save_actions((_action(),))

        document = _read_json(self.actions_directory / "library.json")

        self.assertEqual("robot_llm.actions", document["schema"])
        self.assertEqual(3, document["schema_version"])
        self.assertEqual("unscoped", document["robot_profile_id"])
        self.assertEqual("../../../schemas/action-library.schema.json", document["$schema"])
        self.assertEqual(["action-1"], [item.id for item in self.repository.load_actions()])

    def test_runtime_rejects_legacy_action_collection(self) -> None:
        self.actions_directory.mkdir(parents=True)
        (self.actions_directory / "library.json").write_text(
            json.dumps([_action().to_dict()]),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(JsonDocumentSchemaError, "versioned"):
            self.repository.load_actions()

    def test_duplicate_action_id_or_name_is_rejected(self) -> None:
        duplicate_id = _action(action_id="action-1", name="Other")
        duplicate_name = _action(action_id="action-2", name="Wait")

        self.repository.save_actions((_action(), duplicate_id))
        with self.assertRaisesRegex(JsonDocumentSchemaError, "duplicate action id"):
            self.repository.load_actions()

        self.repository.save_actions((_action(), duplicate_name))
        with self.assertRaisesRegex(JsonDocumentSchemaError, "duplicate action name"):
            self.repository.load_actions()

    def test_future_action_schema_is_rejected(self) -> None:
        self.actions_directory.mkdir(parents=True)
        (self.actions_directory / "library.json").write_text(
            json.dumps({
                "schema": "robot_llm.actions",
                "schema_version": 99,
                "actions": [],
            }),
            encoding="utf-8",
        )

        with self.assertRaises(UnsupportedJsonDocumentVersion):
            self.repository.load_actions()

    def test_text_pose_is_normalized_to_canonical_json_array(self) -> None:
        action = ActionDefinition(
            id="move-1",
            name="Move",
            type=ActionType.MOVE,
            parameters={
                "目标": "机械臂",
                "臂": "左",
                "模式": "move_j",
                "点位": "pose = [1, 2, 3, 4, 5, 6]",
            },
        )
        self.repository.save_actions((action,))

        self.assertEqual(1, normalize_action_catalog(self.actions_directory))

        restored = self.repository.load_actions()[0]
        self.assertEqual([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], restored.parameters["点位"])

    def test_legacy_relocalization_marker_is_flattened(self) -> None:
        action = ActionDefinition(
            id="vision-1",
            name="shijiao-1",
            type=ActionType.VISION_RELOCALIZE,
            parameters={
                "action_mode": "teach",
                "arm": "left",
                "station_name": "station-1",
                "marker": {"width": "0.21", "height": 0.22},
                "move_mode": "move_j",
            },
        )
        self.repository.save_actions((action,))

        self.assertEqual(1, normalize_action_catalog(self.actions_directory))

        parameters = self.repository.load_actions()[0].parameters
        self.assertNotIn("marker", parameters)
        self.assertEqual(0.21, parameters["marker_width"])
        self.assertEqual(0.22, parameters["marker_height"])


class SkillDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        SkillRegistry().reset()

    def tearDown(self) -> None:
        SkillRegistry().reset()
        self.temporary_directory.cleanup()

    def test_recursive_load_is_deterministic(self) -> None:
        _write_skill(self.root / "z" / "second.skill.json", _skill("second"))
        _write_skill(self.root / "a" / "first.skill.json", _skill("first"))

        registry = SkillRegistry()
        count = registry.load_directory(self.root)

        self.assertEqual(2, count)
        self.assertEqual(["first", "second"], sorted(registry.get_all_skill_ids()))

    def test_duplicate_id_rejects_entire_reload(self) -> None:
        _write_skill(self.root / "a" / "stable.skill.json", _skill("stable"))
        registry = SkillRegistry()
        registry.load_directory(self.root)
        _write_skill(self.root / "b" / "duplicate.skill.json", _skill("stable"))

        with self.assertRaisesRegex(JsonDocumentSchemaError, "duplicates skill id"):
            registry.load_directory(self.root)

        self.assertEqual(["stable"], registry.get_all_skill_ids())

    def test_collection_file_is_not_a_runtime_skill_format(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "skill_library.json").write_text(
            json.dumps({"skills": [_skill("legacy").to_dict()]}),
            encoding="utf-8",
        )

        self.assertEqual(0, SkillRegistry().load_directory(self.root))


class BuiltinCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.paths = ApplicationDataPaths(
            root=root,
            robot_profile_id="realman-rm75-dual",
            profile_root=root / "profiles" / "realman-rm75-dual",
            actions_directory=root / "profiles" / "realman-rm75-dual" / "actions",
            workflows_directory=root / "profiles" / "realman-rm75-dual" / "workflows",
            workflow_drafts_directory=root / "profiles" / "realman-rm75-dual" / "drafts",
            skills_directory=root / "skills",
            trajectories_directory=(
                root / "profiles" / "realman-rm75-dual" / "trajectories"
            ),
        )
        SkillRegistry().reset()

    def tearDown(self) -> None:
        SkillRegistry().reset()
        self.temporary_directory.cleanup()

    def test_missing_catalogs_create_empty_user_data_once(self) -> None:
        installer = BuiltinDataInstaller(self.paths)

        first = installer.install_missing()
        self.assertTrue(self.paths.trajectories_directory.is_dir())
        original = (self.paths.actions_directory / "library.json").read_bytes()
        second = installer.install_missing()
        report = validate_catalogs(
            self.paths.actions_directory,
            self.paths.skills_directory,
            robot_profile_id=self.paths.robot_profile_id,
        )

        self.assertEqual(4, len(first.created_files))
        self.assertEqual((), second.created_files)
        self.assertEqual(original, (self.paths.actions_directory / "library.json").read_bytes())
        self.assertEqual(0, report.action_count)
        self.assertEqual(0, report.skill_count)
        self.assertEqual([], list(self.paths.skills_directory.rglob("*.skill.json")))


class CatalogMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        SkillRegistry().reset()

    def tearDown(self) -> None:
        SkillRegistry().reset()
        self.temporary_directory.cleanup()

    def test_legacy_collections_migrate_to_valid_profiled_catalogs(self) -> None:
        legacy_actions = self.root / "actions_library.json"
        legacy_skills = self.root / "skill_library.json"
        legacy_actions.write_text(
            json.dumps({
                "schema": "robot_llm.actions",
                "schema_version": 1,
                "actions": [_action().to_dict()],
            }),
            encoding="utf-8",
        )
        legacy_skills.write_text(
            json.dumps({
                "schema": "robot_llm.skills",
                "schema_version": 1,
                "skills": [_skill("demo").to_dict()],
            }),
            encoding="utf-8",
        )

        report = migrate_catalogs(
            legacy_actions_file=legacy_actions,
            legacy_skills_file=legacy_skills,
            actions_directory=self.root / "actions",
            skills_directory=self.root / "skills",
        )

        self.assertEqual(1, report.action_count)
        self.assertEqual(1, report.skill_count)
        self.assertEqual(2, len(report.written_files))
        self.assertTrue((self.root / "skills" / "move" / "demo.skill.json").is_file())
        self.assertTrue(legacy_actions.is_file())
        self.assertTrue(legacy_skills.is_file())


def _action(action_id: str = "action-1", name: str = "Wait") -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        name=name,
        type=ActionType.WAIT,
        parameters={"wait_seconds": 1.0},
    )


def _skill(skill_id: str) -> Skill:
    return Skill(
        id=skill_id,
        name=f"Skill {skill_id}",
        category=SkillCategory.MOVE,
        description="test skill",
        steps=[
            SkillStep(
                step_id="1",
                action_name="Wait",
                action_type="WAIT",
                parameters={"wait_seconds": 1.0},
            )
        ],
    )


def _write_skill(path: Path, skill: Skill) -> None:
    write_single_document(path, SKILL_DOCUMENT, skill.to_dict())


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


if __name__ == "__main__":
    unittest.main()
