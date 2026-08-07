from __future__ import annotations

import unittest
from typing import cast

from src.domain.models import ActionType
from src.skill_system import (
    Skill,
    SkillCategory,
    SkillEngine,
    SkillMatchResult,
    SkillParameter,
    SkillParameterType,
    SkillRegistry,
    SkillStep,
    ValidationCode,
)
from src.skill_system.builtin_catalog import get_builtin_skills


class _SkillRegistry:
    def __init__(self, skill: Skill) -> None:
        self._skill = skill

    def get_skill(self, skill_id: str) -> Skill | None:
        if skill_id == self._skill.id:
            return self._skill
        return None


def _skill_with_action_types(*action_types: object) -> Skill:
    aliases = {
        alias.lower(): action_type
        for action_type in ActionType
        for alias in (action_type.name, action_type.value)
    }

    def valid_parameters(raw_action_type: object) -> dict:
        if not isinstance(raw_action_type, str):
            return {}
        action_type = aliases.get(raw_action_type.lower())
        return {
            ActionType.MOVE: {
                "目标": "机械臂",
                "臂": "左",
                "模式": "move_j",
                "点位": [0, 0, 0, 0, 0, 0],
            },
            ActionType.BASE_MOVE: {"move_mode": "position"},
            ActionType.MANIPULATE: {
                "执行器": "夹爪",
                "编号": 1,
                "操作": "开",
            },
            ActionType.INSPECT: {"Sensor_ID": "2"},
            ActionType.WAIT: {},
            ActionType.CHANGE_GUN: {},
            ActionType.VISION_CAPTURE: {},
            ActionType.VISION_RELOCALIZE: {"station_name": "station-a"},
            ActionType.TRAJECTORY: {"file_path": "trajectory.json"},
        }.get(action_type, {})

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
                parameters=valid_parameters(action_type),
            )
            for index, action_type in enumerate(action_types)
        ],
    )


def _match(
    *,
    skill_id: str = "test-skill",
    extracted_params: dict | None = None,
) -> SkillMatchResult:
    return SkillMatchResult(
        skill_id=skill_id,
        skill_name="test skill",
        confidence=1.0,
        extracted_params=extracted_params or {},
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


class SkillEngineParameterTests(unittest.TestCase):
    @staticmethod
    def _default_skill(skill_id: str) -> Skill:
        return next(
            skill
            for skill in get_builtin_skills()
            if skill.id == skill_id
        )

    def test_explicit_binding_replaces_action_parameter(self):
        skill = self._default_skill("absorb_liquid")
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match(
            skill_id=skill.id,
            extracted_params={"volume": 750},
        ))

        self.assertTrue(validation.is_valid)
        self.assertEqual(750, sequence[1].definition.parameters["容量"])

    def test_every_default_skill_expands_through_action_schema(self):
        for skill in get_builtin_skills():
            with self.subTest(skill_id=skill.id):
                engine = SkillEngine(
                    cast(SkillRegistry, _SkillRegistry(skill))
                )

                sequence, validation = engine.parse_and_expand(
                    _match(skill_id=skill.id)
                )

                self.assertTrue(validation.is_valid, validation.message)
                self.assertEqual(len(skill.steps), len(sequence))

    def test_skill_serialization_preserves_parameter_contract(self):
        skill = self._default_skill("absorb_liquid")

        restored = Skill.from_dict(skill.to_dict())

        self.assertEqual(skill, restored)

    def test_legacy_skill_without_bindings_is_not_accepted(self):
        data = self._default_skill("absorb_liquid").to_dict()
        del data["steps"][0]["parameter_bindings"]

        with self.assertRaises(KeyError):
            Skill.from_dict(data)

    def test_default_skill_parameter_is_applied(self):
        skill = self._default_skill("inspect_sensor")
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(
            _match(skill_id=skill.id)
        )

        self.assertTrue(validation.is_valid)
        self.assertEqual(
            "2",
            sequence[0].definition.parameters["Sensor_ID"],
        )
        self.assertEqual(
            0.0,
            sequence[0].definition.parameters["Threshold"],
        )

    def test_wrong_skill_parameter_type_is_rejected(self):
        skill = self._default_skill("absorb_liquid")
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match(
            skill_id=skill.id,
            extracted_params={"volume": "750"},
        ))

        self.assertEqual([], sequence)
        self.assertIs(
            ValidationCode.INVALID_SKILL_PARAMETERS,
            validation.code,
        )

    def test_unknown_skill_parameter_is_rejected(self):
        skill = self._default_skill("absorb_liquid")
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match(
            skill_id=skill.id,
            extracted_params={"amount": 750},
        ))

        self.assertEqual([], sequence)
        self.assertIs(
            ValidationCode.INVALID_SKILL_PARAMETERS,
            validation.code,
        )

    def test_action_schema_range_is_enforced_after_binding(self):
        skill = self._default_skill("absorb_liquid")
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match(
            skill_id=skill.id,
            extracted_params={"volume": 10001},
        ))

        self.assertEqual([], sequence)
        self.assertIs(
            ValidationCode.INVALID_ACTION_PARAMETERS,
            validation.code,
        )
        self.assertIn("容量", validation.message)

    def test_binding_unit_mismatch_is_rejected(self):
        skill = Skill(
            id="test-skill",
            name="bad unit",
            category=SkillCategory.COMPOUND,
            description="unit contract",
            parameters=[
                SkillParameter(
                    name="volume",
                    param_label="容量",
                    type=SkillParameterType.INTEGER,
                    description="volume",
                    default=500,
                    unit="ml",
                ),
            ],
            steps=[
                SkillStep(
                    step_id="step-1",
                    action_name="absorb",
                    action_type="MANIPULATE",
                    parameters={
                        "执行器": "吸液枪",
                        "操作": "吸",
                    },
                    parameter_bindings={"volume": "容量"},
                ),
            ],
        )
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match())

        self.assertEqual([], sequence)
        self.assertIs(
            ValidationCode.INVALID_PARAMETER_BINDING,
            validation.code,
        )

    def test_unbound_declared_parameter_is_rejected(self):
        skill = Skill(
            id="test-skill",
            name="unbound",
            category=SkillCategory.COMPOUND,
            description="binding contract",
            parameters=[
                SkillParameter(
                    name="unused",
                    param_label="未使用",
                    type=SkillParameterType.BOOLEAN,
                    description="unused input",
                    default=False,
                ),
            ],
            steps=[
                SkillStep(
                    step_id="step-1",
                    action_name="wait",
                    action_type="WAIT",
                    parameters={},
                ),
            ],
        )
        engine = SkillEngine(cast(SkillRegistry, _SkillRegistry(skill)))

        sequence, validation = engine.parse_and_expand(_match())

        self.assertEqual([], sequence)
        self.assertIs(
            ValidationCode.INVALID_PARAMETER_BINDING,
            validation.code,
        )


if __name__ == "__main__":
    unittest.main()
