"""
Skill 解析引擎
核心业务逻辑：将 LLM 解析结果展开为可执行的 SequenceItem 列表
"""
import logging
from collections.abc import Mapping
import math
from types import MappingProxyType
from typing import List, Dict, Any, Optional, Tuple
from uuid import uuid4

from ..domain.action_schema import (
    get_action_fields,
    validate_action_parameters,
)
from ..domain.models import SequenceItem, SequenceItemStatus, ActionDefinition, ActionType
from .models import (
    Skill,
    SkillMatchResult,
    SkillParameter,
    SkillParameterType,
    ValidationCode,
    ValidationResult,
)
from .skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

_ACTION_TYPES_BY_ALIAS: Mapping[str, ActionType] = MappingProxyType({
    alias.upper(): action_type
    for action_type in ActionType
    for alias in (action_type.name, action_type.value)
})


class SkillEngine:
    """
    技能解析引擎
    负责：
    1. 加载技能库
    2. 将 LLM 解析结果展开为动作序列
    3. 验证动作序列的合法性
    """

    def __init__(self, registry: Optional[SkillRegistry] = None):
        """
        初始化技能引擎

        Args:
            registry: 可选，技能注册表实例。默认为单例
        """
        self._registry = registry or SkillRegistry()
        logger.info("SkillEngine 初始化完成")

    def load_skills(self, json_path: str) -> int:
        """
        从 JSON 文件加载技能库

        Args:
            json_path: JSON 文件路径，默认为 config 中的路径

        Returns:
            加载的技能数量
        """
        return self._registry.load_from_json(json_path)

    def parse_and_expand(
        self,
        llm_result: SkillMatchResult
    ) -> Tuple[List[SequenceItem], ValidationResult]:
        """
        核心方法：将 LLM 解析结果展开为可执行的 SequenceItem 列表

        Args:
            llm_result: LLM 解析结果

        Returns:
            (动作序列, 验证结果) 元组
        """
        if not llm_result.is_valid():
            validation = ValidationResult.failed(
                ValidationCode.INVALID_SKILL_MATCH,
                f"无效的技能匹配: {llm_result.error or '置信度过低'}",
            )
            return [], validation

        # 获取技能定义
        skill = self._registry.get_skill(llm_result.skill_id)
        if skill is None:
            validation = ValidationResult.failed(
                ValidationCode.SKILL_NOT_FOUND,
                f"技能 {llm_result.skill_id} 不存在",
            )
            return [], validation

        action_type_validation = self._validate_action_types(skill)
        if action_type_validation is not None:
            return [], action_type_validation

        resolved_params, parameter_validation = self._resolve_skill_parameters(
            skill,
            llm_result.extracted_params,
        )
        if parameter_validation is not None:
            return [], parameter_validation

        # 展开技能步骤为 SequenceItem
        items, expansion_validation = self._expand_skill(
            skill,
            resolved_params,
        )
        if expansion_validation is not None:
            return [], expansion_validation

        # 验证动作序列
        validation = self._validate_sequence(items, skill)

        return items, validation

    def _expand_skill(
        self,
        skill: Skill,
        params: Dict[str, Any]
    ) -> Tuple[List[SequenceItem], ValidationResult | None]:
        """
        将技能展开为 SequenceItem 列表

        Args:
            skill: 技能定义
            params: 从用户输入中提取的参数

        Returns:
            (SequenceItem 列表, 可选错误) 元组
        """
        binding_validation = self._validate_parameter_bindings(skill)
        if binding_validation is not None:
            return [], binding_validation

        items: List[SequenceItem] = []
        parameters_by_name = {
            parameter.name: parameter
            for parameter in skill.parameters
        }

        for step in skill.steps:
            action_type = self._resolve_action_type(step.action_type)
            if action_type is None:
                raise ValueError(
                    f"技能 {skill.id} 的步骤 {step.step_id} 包含"
                    f"不支持的动作类型: {step.action_type!r}"
                )

            merged_params = dict(step.parameters)
            for parameter_name, action_field in step.parameter_bindings.items():
                if parameter_name in params:
                    merged_params[action_field] = params[parameter_name]

            fields, variant_issue = get_action_fields(
                action_type,
                merged_params,
            )
            if variant_issue is not None or fields is None:
                message = (
                    variant_issue.message
                    if variant_issue is not None
                    else "无法确定动作参数结构"
                )
                return [], ValidationResult.failed(
                    ValidationCode.INVALID_PARAMETER_BINDING,
                    f"技能 {skill.id} 步骤 {step.step_id} 参数绑定无效: "
                    f"{message}",
                )

            for parameter_name, action_field in step.parameter_bindings.items():
                field_schema = fields.get(action_field)
                if field_schema is None:
                    return [], ValidationResult.failed(
                        ValidationCode.INVALID_PARAMETER_BINDING,
                        f"技能 {skill.id} 步骤 {step.step_id} 将参数 "
                        f"{parameter_name!r} 绑定到了未知动作字段 "
                        f"{action_field!r}",
                    )
                skill_unit = parameters_by_name[parameter_name].unit
                action_unit = field_schema.get("unit", "")
                if skill_unit != action_unit:
                    return [], ValidationResult.failed(
                        ValidationCode.INVALID_PARAMETER_BINDING,
                        f"技能 {skill.id} 步骤 {step.step_id} 的参数 "
                        f"{parameter_name!r} 单位 {skill_unit or '无'} 与动作字段 "
                        f"{action_field!r} 单位 {action_unit or '无'} 不一致",
                    )

            action_validation = validate_action_parameters(
                action_type,
                merged_params,
                apply_defaults=True,
                reject_unknown=True,
            )
            if not action_validation.is_valid:
                return [], ValidationResult.failed(
                    ValidationCode.INVALID_ACTION_PARAMETERS,
                    f"技能 {skill.id} 步骤 {step.step_id} 参数无效: "
                    f"{action_validation.message}",
                )

            # 创建 ActionDefinition
            action_def = ActionDefinition(
                id="",
                name=step.action_name,
                type=action_type,
                parameters=action_validation.parameters,
            )

            # 创建 SequenceItem
            item = SequenceItem(
                uuid=str(uuid4()),
                definition=action_def,
                status=SequenceItemStatus.PENDING
            )

            items.append(item)

        logger.debug(f"展开技能 {skill.id} 为 {len(items)} 个动作")
        return items, None

    def _resolve_skill_parameters(
        self,
        skill: Skill,
        extracted_params: object,
    ) -> Tuple[Dict[str, Any], ValidationResult | None]:
        if not isinstance(extracted_params, dict):
            return {}, ValidationResult.failed(
                ValidationCode.INVALID_SKILL_PARAMETERS,
                f"技能 {skill.id} 的提取参数必须是字典",
            )

        parameters_by_name = {
            parameter.name: parameter
            for parameter in skill.parameters
        }
        if any(
            not isinstance(name, str) or not name
            for name in extracted_params
        ):
            return {}, ValidationResult.failed(
                ValidationCode.INVALID_SKILL_PARAMETERS,
                f"技能 {skill.id} 的参数名必须是非空字符串",
            )
        unknown_names = extracted_params.keys() - parameters_by_name.keys()
        if unknown_names:
            return {}, ValidationResult.failed(
                ValidationCode.INVALID_SKILL_PARAMETERS,
                f"技能 {skill.id} 包含未知输入参数: "
                f"{', '.join(sorted(unknown_names))}",
            )

        resolved: Dict[str, Any] = {}
        for parameter in skill.parameters:
            if parameter.name in extracted_params:
                raw_value = extracted_params[parameter.name]
            else:
                raw_value = parameter.default

            if raw_value is None:
                if parameter.required:
                    return {}, ValidationResult.failed(
                        ValidationCode.INVALID_SKILL_PARAMETERS,
                        f"技能 {skill.id} 缺少必填参数: {parameter.name}",
                    )
                continue

            value, error = self._normalize_skill_parameter(
                parameter,
                raw_value,
            )
            if error is not None:
                return {}, ValidationResult.failed(
                    ValidationCode.INVALID_SKILL_PARAMETERS,
                    f"技能 {skill.id} 参数 {parameter.name} 无效: {error}",
                )
            resolved[parameter.name] = value

        return resolved, None

    @staticmethod
    def _normalize_skill_parameter(
        parameter: SkillParameter,
        value: Any,
    ) -> Tuple[Any, str | None]:
        if parameter.type is SkillParameterType.STRING:
            if not isinstance(value, str):
                return None, "类型必须是 str"
            if parameter.required and not value.strip():
                return None, "不能为空字符串"
            return value, None

        if parameter.type is SkillParameterType.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                return None, "类型必须是 int"
            return value, None

        if parameter.type is SkillParameterType.FLOAT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None, "类型必须是 float"
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                return None, "必须是有限数值"
            return numeric_value, None

        if parameter.type is SkillParameterType.BOOLEAN:
            if not isinstance(value, bool):
                return None, "类型必须是 bool"
            return value, None

        return None, f"不支持的参数类型: {parameter.type!r}"

    @staticmethod
    def _validate_parameter_bindings(
        skill: Skill,
    ) -> ValidationResult | None:
        declared_names = [
            parameter.name
            for parameter in skill.parameters
        ]
        if (
            any(not isinstance(name, str) or not name for name in declared_names)
            or len(set(declared_names)) != len(declared_names)
        ):
            return ValidationResult.failed(
                ValidationCode.INVALID_PARAMETER_BINDING,
                f"技能 {skill.id} 的参数声明名称为空或重复",
            )

        for step in skill.steps:
            if not isinstance(step.parameters, dict):
                return ValidationResult.failed(
                    ValidationCode.INVALID_ACTION_PARAMETERS,
                    f"技能 {skill.id} 步骤 {step.step_id} 的动作参数必须是字典",
                )
            if not isinstance(step.parameter_bindings, dict) or any(
                not isinstance(source, str)
                or not source
                or not isinstance(target, str)
                or not target
                for source, target in step.parameter_bindings.items()
            ):
                return ValidationResult.failed(
                    ValidationCode.INVALID_PARAMETER_BINDING,
                    f"技能 {skill.id} 步骤 {step.step_id} 的参数绑定"
                    "必须使用非空字符串名称",
                )

        declared_name_set = set(declared_names)
        bound_names = {
            parameter_name
            for step in skill.steps
            for parameter_name in step.parameter_bindings
        }

        unknown_names = bound_names - declared_name_set
        if unknown_names:
            return ValidationResult.failed(
                ValidationCode.INVALID_PARAMETER_BINDING,
                f"技能 {skill.id} 绑定了未声明参数: "
                f"{', '.join(sorted(unknown_names))}",
            )

        unbound_names = declared_name_set - bound_names
        if unbound_names:
            return ValidationResult.failed(
                ValidationCode.INVALID_PARAMETER_BINDING,
                f"技能 {skill.id} 存在未绑定参数: "
                f"{', '.join(sorted(unbound_names))}",
            )
        return None

    @staticmethod
    def _resolve_action_type(raw_action_type: object) -> ActionType | None:
        if not isinstance(raw_action_type, str):
            return None
        return _ACTION_TYPES_BY_ALIAS.get(raw_action_type.strip().upper())

    def _validate_action_types(
        self,
        skill: Skill,
    ) -> ValidationResult | None:
        unsupported_steps = [
            f"{step.step_id}={step.action_type!r}"
            for step in skill.steps
            if self._resolve_action_type(step.action_type) is None
        ]
        if not unsupported_steps:
            return None

        supported_types = ", ".join(
            action_type.name for action_type in ActionType
        )
        return ValidationResult.failed(
            ValidationCode.UNSUPPORTED_ACTION_TYPE,
            f"技能 {skill.id} 包含不支持的动作类型: "
            f"{', '.join(unsupported_steps)}；支持类型: {supported_types}",
        )

    def _validate_sequence(
        self,
        items: List[SequenceItem],
        skill: Skill
    ) -> ValidationResult:
        """
        验证动作序列的合法性

        Args:
            items: 动作序列
            skill: 所属技能

        Returns:
            验证结果
        """
        warnings = []

        if not items:
            return ValidationResult.failed(
                ValidationCode.EMPTY_SEQUENCE,
                "动作序列为空",
            )

        # 检查是否有连续操作同一执行器的动作
        executor_usage: Dict[str, int] = {}
        for item in items:
            params = item.definition.parameters
            executor = params.get("执行器", params.get("目标", ""))
            if executor:
                executor_usage[executor] = executor_usage.get(executor, 0) + 1

        # 检查夹爪操作
        gripper_ops = [
            (i, items[i].definition.parameters.get("操作", ""))
            for i in range(len(items))
            if items[i].definition.parameters.get("执行器") == "夹爪"
        ]

        # 如果有夹爪操作，检查是否有打开操作
        if gripper_ops:
            has_open = any(op == "开" for _, op in gripper_ops)
            has_close = any(op == "关" for _, op in gripper_ops)

            if has_close and not has_open:
                warnings.append("夹爪操作可能需要先打开再关闭")

        # 警告：动作数量过多
        if len(items) > 20:
            warnings.append(f"动作序列较长（{len(items)}步），执行时间可能较长")

        return ValidationResult.succeeded(
            f"动作序列验证通过，共 {len(items)} 个动作",
            warnings=tuple(warnings),
        )

    def get_skill_info(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """
        获取技能详细信息

        Args:
            skill_id: 技能ID

        Returns:
            技能信息字典，不存在则返回 None
        """
        skill = self._registry.get_skill(skill_id)
        if skill is None:
            return None

        return {
            "id": skill.id,
            "name": skill.name,
            "category": skill.category.value,
            "description": skill.description,
            "icon": skill.icon,
            "parameters": [p.to_dict() for p in skill.parameters],
            "step_count": len(skill.steps),
            "estimated_time": skill.estimate_total_time(),
            "examples": skill.examples,
        }

    def list_all_skills(self) -> List[Dict[str, Any]]:
        """
        列出所有技能

        Returns:
            技能信息列表
        """
        skills = []
        for skill in self._registry.list_skills():
            skills.append({
                **skill.get_summary(),
                "icon": skill.icon,
                "estimated_time": skill.estimate_total_time(),
            })
        return skills
