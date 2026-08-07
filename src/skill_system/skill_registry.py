"""Skill registry and versioned user skill-library persistence."""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..persistence.json_documents import (
    JsonDocumentSchemaError,
    SingleDocumentSpec,
    load_single_document,
)
from .models import Skill, SkillCategory
from ..domain.action_schema import validate_action_parameters
from ..domain.models import ActionType

logger = logging.getLogger(__name__)
SKILL_DOCUMENT = SingleDocumentSpec(
    schema="robot_llm.skill",
    content_key="skill",
    current_version=2,
    schema_reference="../../schemas/skill.schema.json",
)


class SkillRegistry:
    """
    技能注册表（单例）
    管理所有技能的定义，支持从 JSON 文件加载和查询
    """

    _instance: Optional["SkillRegistry"] = None
    _initialized: bool

    def __new__(cls) -> "SkillRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._skills: Dict[str, Skill] = {}
        self._initialized = True
        logger.info("SkillRegistry 初始化完成")

    # ==================== 核心操作 ====================

    def register(self, skill: Skill) -> None:
        """
        注册一个技能

        Args:
            skill: 技能实例
        """
        if skill.id in self._skills:
            logger.warning(f"技能 {skill.id} 已存在，将被覆盖")
        self._skills[skill.id] = skill
        logger.debug(f"注册技能: {skill.id} - {skill.name}")

    def unregister(self, skill_id: str) -> bool:
        """
        取消注册一个技能

        Args:
            skill_id: 技能ID

        Returns:
            是否成功取消注册
        """
        if skill_id in self._skills:
            del self._skills[skill_id]
            logger.debug(f"取消注册技能: {skill_id}")
            return True
        return False

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """
        获取指定ID的技能

        Args:
            skill_id: 技能ID

        Returns:
            技能实例，不存在则返回 None
        """
        return self._skills.get(skill_id)

    def list_skills(self, category: Optional[SkillCategory] = None) -> List[Skill]:
        """
        列出所有技能，可按分类筛选

        Args:
            category: 可选，按分类筛选

        Returns:
            技能列表
        """
        skills = list(self._skills.values())
        if category is not None:
            skills = [s for s in skills if s.category == category]
        return sorted(skills, key=lambda s: s.name)

    def get_all_skill_ids(self) -> List[str]:
        """获取所有技能ID列表"""
        return list(self._skills.keys())

    def clear(self) -> None:
        """清空所有注册的技能"""
        self._skills.clear()
        logger.info("技能注册表已清空")

    # ==================== 加载与保存 ====================

    def load_directory(self, directory: str | Path) -> int:
        """Validate all skill files, then atomically replace the registry."""
        directory = Path(directory)
        skills = load_skill_documents(directory)
        parsed_skills: dict[str, Skill] = {}
        for skill in skills:
            if skill.id in parsed_skills:
                raise JsonDocumentSchemaError(
                    f"skill directory duplicates skill id {skill.id!r}"
                )
            parsed_skills[skill.id] = skill

        self._skills = parsed_skills
        logger.info("从 %s 加载了 %d 个技能", directory, len(parsed_skills))
        return len(parsed_skills)

    # ==================== 查询方法 ====================

    def search_skills(self, query: str) -> List[Skill]:
        """
        搜索技能（基于名称、标签、描述）

        Args:
            query: 搜索关键词

        Returns:
            匹配的技能列表
        """
        query_lower = query.lower()
        results = []

        for skill in self._skills.values():
            # 匹配名称
            if query_lower in skill.name.lower():
                results.append(skill)
                continue

            # 匹配标签
            for tag in skill.tags:
                if query_lower in tag.lower():
                    results.append(skill)
                    break
            else:
                # 匹配描述
                if query_lower in skill.description.lower():
                    results.append(skill)

        return results

    def get_all_skill_summaries(self) -> List[Dict[str, Any]]:
        """
        获取所有技能的摘要信息（用于 LLM Prompt）

        Returns:
            技能摘要列表
        """
        return [skill.get_summary() for skill in self._skills.values()]

    def get_skill_descriptions_for_prompt(self) -> str:
        """
        生成用于 LLM Prompt 的技能描述文本

        Returns:
            格式化的技能描述字符串
        """
        lines = []

        for skill in self._skills.values():
            param_str = (
                ", ".join([p.param_label for p in skill.parameters]) if skill.parameters else "无"
            )
            example_str = " / ".join(skill.examples[:2]) if skill.examples else ""

            lines.append(f"- 技能ID: {skill.id}")
            lines.append(f"  名称: {skill.name} {skill.icon}")
            lines.append(f"  分类: {skill.category.value}")
            lines.append(f"  描述: {skill.description}")
            lines.append(f"  参数: {param_str}")
            lines.append(f"  示例: {example_str}")
            lines.append("")

        return "\n".join(lines)

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取注册表统计信息"""
        by_category: Dict[str, int] = {}
        for skill in self._skills.values():
            cat_name = skill.category.value
            by_category[cat_name] = by_category.get(cat_name, 0) + 1

        return {
            "total": len(self._skills),
            "by_category": by_category,
        }

    def reset(self) -> None:
        """重置注册表（用于测试）"""
        self._skills.clear()
        self._initialized = False
        SkillRegistry._instance = None


def _parse_skill(path: Path, skill_data: dict[str, Any]) -> Skill:
    try:
        skill = Skill.from_dict(skill_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise JsonDocumentSchemaError(f"{path.name} contains an invalid skill") from exc
    _validate_skill(path, skill)
    return skill


def _validate_skill(path: Path, skill: Skill) -> None:
    parameter_names = [parameter.name for parameter in skill.parameters]
    if len(parameter_names) != len(set(parameter_names)):
        raise JsonDocumentSchemaError(f"{path.name} contains duplicate parameter names")
    step_ids = [step.step_id for step in skill.steps]
    if not step_ids or len(step_ids) != len(set(step_ids)):
        raise JsonDocumentSchemaError(f"{path.name} requires unique non-empty step ids")
    for step in skill.steps:
        action_type = _resolve_action_type(path, step.action_type)
        validation = validate_action_parameters(action_type, step.parameters)
        if not validation.is_valid:
            raise JsonDocumentSchemaError(
                f"{path.name} step {step.step_id!r} has invalid parameters: "
                f"{validation.message}"
            )
        for parameter_name, action_field in step.parameter_bindings.items():
            if parameter_name not in parameter_names:
                raise JsonDocumentSchemaError(
                    f"{path.name} step {step.step_id!r} binds unknown skill parameter "
                    f"{parameter_name!r}"
                )
            if action_field not in step.parameters:
                raise JsonDocumentSchemaError(
                    f"{path.name} step {step.step_id!r} binds missing action field "
                    f"{action_field!r}"
                )


def _resolve_action_type(path: Path, value: str) -> ActionType:
    normalized = value.strip().upper()
    for action_type in ActionType:
        if normalized in {action_type.name, action_type.value.upper()}:
            return action_type
    raise JsonDocumentSchemaError(
        f"{path.name} declares unsupported action type {value!r}"
    )


def load_skill_documents(directory: Path) -> tuple[Skill, ...]:
    """Read a directory deterministically without changing a registry."""
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    paths = sorted(
        directory.rglob("*.skill.json"),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    skills: list[Skill] = []
    ids: set[str] = set()
    for path in paths:
        skill = _parse_skill(path, load_single_document(path, SKILL_DOCUMENT))
        if skill.id in ids:
            raise JsonDocumentSchemaError(
                f"{path.name} duplicates skill id {skill.id!r}"
            )
        ids.add(skill.id)
        skills.append(skill)
    return tuple(skills)
