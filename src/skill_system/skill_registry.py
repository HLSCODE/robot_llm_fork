"""
技能注册表
单例模式，管理所有技能的定义和查询
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from .models import Skill, SkillCategory

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    技能注册表（单例）
    管理所有技能的定义，支持从 JSON 文件加载和查询
    """
    _instance: Optional['SkillRegistry'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
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

    def load_from_json(self, json_path: str | Path) -> int:
        """
        从 JSON 文件加载技能库

        Args:
            json_path: JSON 文件路径

        Returns:
            加载的技能数量
        """
        json_path = Path(json_path)

        if not json_path.exists():
            logger.error(f"技能库文件不存在: {json_path}")
            return 0

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            skills_data = data.get("skills", [])
            count = 0

            for skill_data in skills_data:
                try:
                    skill = Skill.from_dict(skill_data)
                    self.register(skill)
                    count += 1
                except Exception as e:
                    logger.error(f"加载技能失败: {skill_data.get('id', 'unknown')}, 错误: {e}")

            logger.info(f"从 {json_path} 加载了 {count} 个技能")
            return count

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return 0
        except Exception as e:
            logger.error(f"加载技能库失败: {e}")
            return 0

    def save_to_json(self, json_path: str | Path) -> bool:
        """
        将当前技能库保存到 JSON 文件

        Args:
            json_path: 保存路径

        Returns:
            是否保存成功
        """
        json_path = Path(json_path)

        try:
            # 确保目录存在
            json_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "skills": [skill.to_dict() for skill in self._skills.values()]
            }

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"技能库已保存到 {json_path}")
            return True

        except Exception as e:
            logger.error(f"保存技能库失败: {e}")
            return False

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
            param_str = ", ".join([p.param_label for p in skill.parameters]) if skill.parameters else "无"
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
