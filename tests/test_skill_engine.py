from __future__ import annotations

import unittest
from typing import cast

from src.core.models import ActionType
from src.skill_system import (
    Skill,
    SkillCategory,
    SkillEngine,
    SkillMatchResult,
    SkillRegistry,
    SkillStep,
    ValidationCode,
)


class _SkillRegistry:
    def __init__(self, skill: Skill) -> None:
        self._skill = skill

    def get_skill(self, skill_id: str) -> Skill | None:
        if skill_id == self._skill.id:
            return self._skill
        return None


def _skill_with_action_types(*action_types: object) -> Skill:
    return Skill(
        id="test-skill",
        name="test skill",
        category=SkillCategory.COMPOUND,
        description="skill engine contract test",
        steps=[
            SkillStep(
                step_id=f"step-{index}",
                action_name=f"action-{index}",
                action_type=cast(str, action_type),
                parameters={},
            )
            for index, action_type in enumerate(action_types)
        ],
    )


def _match() -> SkillMatchResult:
    return SkillMatchResult(
        skill_id="test-skill",
        skill_name="test skill",
        confidence=1.0,
        extracted_params={},
        reasoning="test",
    )


class SkillEngineActionTypeTests(unittest.TestCase):
    def test_unknown_action_type_is_rejected_instead_of_becoming_move(self):
        skill = _skill_with_action_types("MOVE", "MOVEE")
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match())

        self.assertEqual([], sequence)
        self.assertFalse(validation.is_valid)
        self.assertIs(
            ValidationCode.UNSUPPORTED_ACTION_TYPE,
            validation.code,
        )
        self.assertIn("step-1='MOVEE'", validation.message)
        self.assertEqual(
            "unsupported_action_type",
            validation.to_dict()["code"],
        )

    def test_non_string_action_type_is_rejected_explicitly(self):
        skill = _skill_with_action_types(None)
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match())

        self.assertEqual([], sequence)
        self.assertIs(
            ValidationCode.UNSUPPORTED_ACTION_TYPE,
            validation.code,
        )
        self.assertIn("step-0=None", validation.message)

    def test_action_type_names_and_protocol_values_share_one_mapping(self):
        aliases_and_expected = [
            (alias, action_type)
            for action_type in ActionType
            for alias in (action_type.name, action_type.value)
        ]
        skill = _skill_with_action_types(
            *(alias.lower() for alias, _expected in aliases_and_expected)
        )
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match())

        self.assertTrue(validation.is_valid)
        self.assertIs(ValidationCode.VALID, validation.code)
        self.assertEqual(
            [expected for _alias, expected in aliases_and_expected],
            [item.definition.type for item in sequence],
        )


if __name__ == "__main__":
    unittest.main()
