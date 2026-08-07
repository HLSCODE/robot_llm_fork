from __future__ import annotations

import unittest

from src.application import CommandCatalog, CommandResolutionStatus
from src.configuration.settings import RuntimeSettings
from src.domain.commands import ActionCommand, SkillCommand, WorkflowCommand
from src.domain.models import ActionDefinition, ActionType


class _Composition:
    def __init__(self) -> None:
        self.action = ActionDefinition(
            id="wait.once",
            name="等待一次",
            type=ActionType.WAIT,
            parameters={"seconds": 1},
        )

    def list_actions(self):
        return [self.action]

    def list_workflows(self):
        return ["sample.workflow.json"]


class _AmbiguousComposition(_Composition):
    def list_actions(self):
        return [
            ActionDefinition(
                id="return.once",
                name="归位",
                type=ActionType.WAIT,
                parameters={"wait_seconds": 1},
            )
        ]


class _Skills:
    def list_all_skills(self):
        return [{
            "id": "move_home",
            "name": "回到安全位",
            "examples": ["回安全位"],
            "tags": ["归位"],
        }]


class CommandCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = CommandCatalog(
            _Composition(),
            _Skills(),
            RuntimeSettings(
                command_arm_relative_step_mm=10,
                command_arm_relative_max_mm=50,
                command_base_relative_step_cm=5,
                command_base_relative_max_cm=20,
            ),
        )

    def test_gripper_requires_an_explicit_arm(self):
        result = self.catalog.resolve("打开夹爪")

        self.assertEqual(CommandResolutionStatus.AMBIGUOUS, result.status)
        self.assertFalse(result.should_fallback_to_llm)

    def test_right_gripper_command_is_typed(self):
        result = self.catalog.resolve("关闭右夹爪")

        self.assertEqual(CommandResolutionStatus.MATCHED, result.status)
        self.assertIsInstance(result.command, ActionCommand)
        assert isinstance(result.command, ActionCommand)
        self.assertEqual(ActionType.MANIPULATE, result.command.action_type)
        self.assertEqual(
            {"执行器": "夹爪", "编号": 2, "操作": "关"},
            result.command.parameters,
        )

    def test_arm_relative_command_converts_units_and_enforces_limit(self):
        result = self.catalog.resolve("左机械臂向上2厘米")

        self.assertEqual(CommandResolutionStatus.MATCHED, result.status)
        assert isinstance(result.command, ActionCommand)
        self.assertEqual(20.0, result.command.parameters["z_mm"])
        self.assertEqual("左", result.command.parameters["臂"])

        rejected = self.catalog.resolve("左机械臂向上6厘米")
        self.assertEqual(CommandResolutionStatus.INVALID, rejected.status)
        self.assertFalse(rejected.should_fallback_to_llm)

    def test_relative_motion_without_device_never_falls_back_to_llm(self):
        result = self.catalog.resolve("向前一点")

        self.assertEqual(CommandResolutionStatus.AMBIGUOUS, result.status)
        self.assertFalse(result.should_fallback_to_llm)

    def test_multi_device_command_is_rejected_instead_of_partially_executed(self):
        result = self.catalog.resolve("打开左夹爪然后底盘向前一点")

        self.assertEqual(CommandResolutionStatus.AMBIGUOUS, result.status)
        self.assertIsNone(result.command)

    def test_base_relative_command_uses_configured_default_step(self):
        result = self.catalog.resolve("底盘向左一点")

        self.assertEqual(CommandResolutionStatus.MATCHED, result.status)
        assert isinstance(result.command, ActionCommand)
        self.assertEqual(5.0, result.command.parameters["y"])
        self.assertEqual(0.0, result.command.parameters["x"])

    def test_exact_skill_and_workflow_names_are_resolved(self):
        skill = self.catalog.resolve("归位")
        workflow = self.catalog.resolve("sample")

        self.assertIsInstance(skill.command, SkillCommand)
        self.assertIsInstance(workflow.command, WorkflowCommand)

    def test_catalog_exposes_all_command_kinds(self):
        kinds = {entry["kind"] for entry in self.catalog.entries()}

        self.assertEqual(
            {"action", "skill", "workflow", "execution_control"},
            kinds,
        )

    def test_duplicate_alias_across_command_kinds_is_ambiguous(self):
        catalog = CommandCatalog(
            _AmbiguousComposition(),
            _Skills(),
            RuntimeSettings(),
        )

        result = catalog.resolve("归位")

        self.assertEqual(CommandResolutionStatus.AMBIGUOUS, result.status)
        self.assertFalse(result.should_fallback_to_llm)


if __name__ == "__main__":
    unittest.main()
