from __future__ import annotations

import unittest

from src.domain.commands import (
    ActionCommand,
    ExecutionControlCommand,
    SkillCommand,
    WorkflowCommand,
    command_from_dict,
)


class TypedCommandTests(unittest.TestCase):
    def test_every_command_kind_parses_to_a_distinct_type(self):
        commands = [
            command_from_dict({
                "kind": "action",
                "action_type": "WAIT",
                "parameters": {"seconds": 1},
            }),
            command_from_dict({
                "kind": "skill",
                "skill_id": "move_home",
            }),
            command_from_dict({
                "kind": "workflow",
                "workflow_name": "sample.workflow.json",
            }),
            command_from_dict({
                "kind": "execution_control",
                "action": "cancel",
            }),
        ]

        self.assertEqual(
            [ActionCommand, SkillCommand, WorkflowCommand, ExecutionControlCommand],
            [type(command) for command in commands],
        )

    def test_unknown_fields_are_rejected_at_the_boundary(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            command_from_dict({
                "kind": "skill",
                "skill_id": "move_home",
                "device": "left",
            })

    def test_command_parameters_are_defensively_copied(self):
        parameters = {"nested": {"value": 1}}
        command = command_from_dict({
            "kind": "skill",
            "skill_id": "move_home",
            "parameters": parameters,
        })
        parameters["nested"]["value"] = 2

        assert isinstance(command, SkillCommand)
        self.assertEqual(1, command.parameters["nested"]["value"])


if __name__ == "__main__":
    unittest.main()
