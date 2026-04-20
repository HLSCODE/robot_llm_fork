"""
Skill 解析引擎
核心业务逻辑：将 LLM 解析结果展开为可执行的 SequenceItem 列表
"""
import logging
from typing import List, Dict, Any, Optional, Tuple
from uuid import uuid4

from ..core.models import SequenceItem, SequenceItemStatus, ActionDefinition, ActionType
from .models import Skill, SkillMatchResult, ValidationResult
from .skill_registry import SkillRegistry

logger = logging.getLogger(__name__)


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
        self._action_type_map = {
            "MOVE": ActionType.MOVE,
            "MOVE_TO_POINT": ActionType.MOVE,
            "MANIPULATE": ActionType.MANIPULATE,
            "ARM_ACTION": ActionType.MANIPULATE,
            "INSPECT": ActionType.INSPECT,
            "INSPECT_AND_OUTPUT": ActionType.INSPECT,
            "CHANGE_GUN": ActionType.CHANGE_GUN,
        }
        logger.info("SkillEngine 初始化完成")

    def load_skills(self, json_path: Optional[str] = None) -> int:
        """
        从 JSON 文件加载技能库

        Args:
            json_path: JSON 文件路径，默认为 config 中的路径

        Returns:
            加载的技能数量
        """
        if json_path is None:
            from ..core.config_loader import Config
            json_path = str(Config.get_skill_library_path())

        count = self._registry.load_from_json(json_path)
        if count == 0:
            # 尝试加载默认技能
            from .default_skills import get_default_skills
            default_skills = get_default_skills()
            for skill in default_skills:
                self._registry.register(skill)
            count = len(default_skills)
            logger.info(f"使用默认技能库，共 {count} 个技能")

        return count

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
            validation = ValidationResult(
                is_valid=False,
                message=f"无效的技能匹配: {llm_result.error or '置信度过低'}",
                warnings=[]
            )
            return [], validation

        # 获取技能定义
        skill = self._registry.get_skill(llm_result.skill_id)
        if skill is None:
            validation = ValidationResult(
                is_valid=False,
                message=f"技能 {llm_result.skill_id} 不存在",
                warnings=[]
            )
            return [], validation

        # 展开技能步骤为 SequenceItem
        items = self._expand_skill(skill, llm_result.extracted_params)

        # 验证动作序列
        validation = self._validate_sequence(items, skill)

        return items, validation

    def _expand_skill(
        self,
        skill: Skill,
        params: Dict[str, Any]
    ) -> List[SequenceItem]:
        """
        将技能展开为 SequenceItem 列表

        Args:
            skill: 技能定义
            params: 从用户输入中提取的参数

        Returns:
            SequenceItem 列表
        """
        items = []

        for step in skill.steps:
            # 将 action_type 字符串映射到 ActionType 枚举
            action_type_str = step.action_type.upper()
            action_type = self._action_type_map.get(
                action_type_str,
                ActionType.MOVE  # 默认值
            )

            # 合并参数：技能定义的参数 + 用户提供的参数
            merged_params = {**step.parameters}

            # 处理参数替换
            for param_name, param_value in params.items():
                # 如果用户提供了参数值，使用用户提供的值
                # 这里可以添加参数映射逻辑
                if param_name == "volume" and "容量" in merged_params:
                    merged_params["容量"] = param_value

            # 创建 ActionDefinition
            action_def = ActionDefinition(
                id="",
                name=step.action_name,
                type=action_type,
                parameters=merged_params
            )

            # 创建 SequenceItem
            item = SequenceItem(
                uuid=str(uuid4()),
                definition=action_def,
                status=SequenceItemStatus.PENDING
            )

            items.append(item)

        logger.debug(f"展开技能 {skill.id} 为 {len(items)} 个动作")
        return items

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
            return ValidationResult(
                is_valid=False,
                message="动作序列为空",
                warnings=[]
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

        return ValidationResult(
            is_valid=True,
            message=f"动作序列验证通过，共 {len(items)} 个动作",
            warnings=warnings
        )

    def get_skill_preview(
        self,
        skill_id: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        获取技能预览（不实际执行，只展示步骤）

        Args:
            skill_id: 技能ID
            params: 可选，参数值

        Returns:
            步骤预览列表，不存在则返回 None
        """
        skill = self._registry.get_skill(skill_id)
        if skill is None:
            return None

        params = params or {}
        preview = []

        for step in skill.steps:
            preview.append({
                "step_id": step.step_id,
                "action_name": step.action_name,
                "description": step.description,
                "estimated_time": step.estimated_time,
                "action_type": step.action_type,
            })

        return preview

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
                "id": skill.id,
                "name": skill.name,
                "category": skill.category.value,
                "description": skill.description,
                "icon": skill.icon,
                "step_count": len(skill.steps),
                "estimated_time": skill.estimate_total_time(),
            })
        return skills
