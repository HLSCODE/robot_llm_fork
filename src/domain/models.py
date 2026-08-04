"""Application-independent action and sequence domain models."""

from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, List, Union
from uuid import uuid4


class ActionType(Enum):
    MOVE = "MOVE_TO_POINT"
    BASE_MOVE = "BASE_MOVE"  # 底盘移动（通过 move_mode 区分位置移动和距离移动）
    MANIPULATE = "ARM_ACTION"
    INSPECT = "INSPECT_AND_OUTPUT"
    WAIT = "WAIT"
    CHANGE_GUN = "CHANGE_GUN"
    VISION_CAPTURE = "VISION_CAPTURE"
    VISION_RELOCALIZE = "VISION_RELOCALIZE"
    TRAJECTORY = "TRAJECTORY"



class SequenceItemStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class ActionDefinition:
    id: str
    name: str
    type: ActionType
    parameters: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "parameters": self.parameters
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionDefinition':
        return cls(
            id=data.get("id", ""),
            name=data["name"],
            type=ActionType(data["type"]),
            parameters=data["parameters"]
        )


@dataclass
class SequenceItem:
    uuid: str
    definition: ActionDefinition
    status: SequenceItemStatus = SequenceItemStatus.PENDING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "definition": self.definition.to_dict(),
            "status": self.status.value
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SequenceItem':
        return cls(
            uuid=data["uuid"],
            definition=ActionDefinition.from_dict(data["definition"]),
            status=SequenceItemStatus(data.get("status", "PENDING"))
        )

    @classmethod
    def from_definition(cls, definition: ActionDefinition) -> 'SequenceItem':
        return cls(
            uuid=str(uuid4()),
            definition=definition
        )


@dataclass
class LoopBlock:
    """循环块容器 — 将一组动作包裹起来循环执行 N 次"""
    uuid: str
    items: List[SequenceItem]
    repeat_count: int
    current_iteration: int = 0  # 执行时追踪当前轮次

    @property
    def total_steps(self) -> int:
        """循环展开后的总步数"""
        return len(self.items) * self.repeat_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "loop",
            "uuid": self.uuid,
            "items": [item.to_dict() for item in self.items],
            "repeat_count": self.repeat_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LoopBlock':
        return cls(
            uuid=data.get("uuid", str(uuid4())),
            items=[SequenceItem.from_dict(item) for item in data.get("items", [])],
            repeat_count=data.get("repeat_count", 2),
        )

    @classmethod
    def from_sequence_items(cls, items: List[SequenceItem], repeat_count: int) -> 'LoopBlock':
        """从已有 SequenceItem 列表创建循环块（深拷贝子项）"""
        cloned = [SequenceItem.from_dict(item.to_dict()) for item in items]
        return cls(
            uuid=str(uuid4()),
            items=cloned,
            repeat_count=repeat_count,
        )


# 序列条目的联合类型：普通动作 或 循环块
SequenceEntry = Union[SequenceItem, LoopBlock]
