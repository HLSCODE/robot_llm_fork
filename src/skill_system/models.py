"""
Skill 数据模型
定义技能系统的核心数据结构
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class SkillCategory(Enum):
    """技能分类"""
    GRAB = "抓取"          # 抓取类技能
    MOVE = "移动"          # 移动类技能
    INSPECT = "检测"       # 检测类技能
    TOOL = "工具"          # 工具更换类技能
    COMPOUND = "复合"      # 复合类技能


@dataclass
class SkillParameter:
    """
    技能参数定义
    描述技能需要用户提供的参数信息
    """
    name: str              # 参数名（英文，用于代码中引用）
    param_label: str       # 参数显示名称（中文）
    type: str              # 参数类型: str, int, float, bool
    description: str       # 参数描述
    default: Any           # 默认值
    required: bool = True  # 是否必填

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "param_label": self.param_label,
            "type": self.type,
            "description": self.description,
            "default": self.default,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SkillParameter':
        return cls(
            name=data["name"],
            param_label=data.get("param_label", data["name"]),
            type=data["type"],
            description=data.get("description", ""),
            default=data.get("default"),
            required=data.get("required", True),
        )


@dataclass
class SkillStep:
    """
    技能中的单个步骤
    对应现有 ActionDefinition 的一个原子动作
    """
    step_id: str                        # 步骤ID
    action_name: str                    # 调用的动作名（对应 actions_library.json 中的 name）
    action_type: str                     # MOVE / MANIPULATE / INSPECT / CHANGE_GUN
    parameters: Dict[str, Any]          # 动作参数
    description: str = ""               # 步骤描述
    estimated_time: float = 2.0          # 预估执行时间（秒）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action_name": self.action_name,
            "action_type": self.action_type,
            "parameters": self.parameters,
            "description": self.description,
            "estimated_time": self.estimated_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SkillStep':
        return cls(
            step_id=data["step_id"],
            action_name=data["action_name"],
            action_type=data["action_type"],
            parameters=data.get("parameters", {}),
            description=data.get("description", ""),
            estimated_time=data.get("estimated_time", 2.0),
        )


@dataclass
class Skill:
    """
    完整技能定义
    由多个原子动作步骤组成的复合技能
    """
    id: str                             # 技能唯一标识
    name: str                           # 技能显示名称
    category: SkillCategory             # 技能分类
    description: str                    # 技能描述
    icon: str = "🤖"                    # 图标
    parameters: List[SkillParameter] = field(default_factory=list)
    steps: List[SkillStep] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)    # 示例说法
    tags: List[str] = field(default_factory=list)       # 标签（用于匹配）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "icon": self.icon,
            "parameters": [p.to_dict() for p in self.parameters],
            "steps": [s.to_dict() for s in self.steps],
            "examples": self.examples,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Skill':
        category_str = data.get("category", "复合")
        # 兼容旧格式的 category 字符串
        if isinstance(category_str, str):
            category = SkillCategory.COMPOUND
            for cat in SkillCategory:
                if cat.value == category_str:
                    category = cat
                    break
        else:
            category = category_str

        return cls(
            id=data["id"],
            name=data["name"],
            category=category,
            description=data.get("description", ""),
            icon=data.get("icon", "🤖"),
            parameters=[SkillParameter.from_dict(p) for p in data.get("parameters", [])],
            steps=[SkillStep.from_dict(s) for s in data.get("steps", [])],
            examples=data.get("examples", []),
            tags=data.get("tags", []),
        )

    def get_summary(self) -> Dict[str, Any]:
        """获取技能的摘要信息（用于 LLM Prompt）"""
        param_info = []
        for p in self.parameters:
            param_info.append(f"- {p.param_label}({p.name}): {p.description}")

        step_descriptions = [s.description or s.action_name for s in self.steps]

        return {
            "id": self.id,
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "parameters": param_info,
            "step_count": len(self.steps),
            "step_descriptions": step_descriptions,
            "examples": self.examples[:3],  # 只取前3个示例
            "tags": self.tags,
        }

    def estimate_total_time(self) -> float:
        """预估技能总执行时间"""
        return sum(step.estimated_time for step in self.steps)


@dataclass
class SkillMatchResult:
    """
    LLM 解析后的结果
    包含匹配的技能ID、提取的参数和置信度
    """
    skill_id: str                       # 匹配的技能ID
    skill_name: str                     # 技能名称
    confidence: float                   # 置信度 0.0 ~ 1.0
    extracted_params: Dict[str, Any]   # 从用户输入中提取的参数
    reasoning: str                      # 分析思路
    error: Optional[str] = None         # 错误信息（无法匹配时）

    def is_valid(self) -> bool:
        """是否有效匹配（置信度 >= 0.5）"""
        return self.confidence >= 0.5 and self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "confidence": self.confidence,
            "extracted_params": self.extracted_params,
            "reasoning": self.reasoning,
            "error": self.error,
        }


@dataclass
class ValidationResult:
    """动作序列验证结果"""
    is_valid: bool
    message: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "message": self.message,
            "warnings": self.warnings,
        }
