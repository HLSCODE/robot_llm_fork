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
    robot_profile_id: str = "unscoped"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "parameters": self.parameters,
            "robot_profile_id": self.robot_profile_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionDefinition':
        return cls(
            id=data.get("id", ""),
            name=data["name"],
            type=ActionType(data["type"]),
            parameters=data["parameters"],
            robot_profile_id=str(data.get("robot_profile_id", "unscoped")),
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
    items: List['SequenceEntry']
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
            items=[sequence_entry_from_dict(item) for item in data.get("items", [])],
            repeat_count=data.get("repeat_count", 2),
        )

    @classmethod
    def from_sequence_items(cls, items: List[SequenceItem], repeat_count: int) -> 'LoopBlock':
        """从已有 SequenceItem 列表创建循环块（深拷贝子项）"""
        cloned: List[SequenceEntry] = [
            SequenceItem.from_dict(item.to_dict()) for item in items
        ]
        return cls(
            uuid=str(uuid4()),
            items=cloned,
            repeat_count=repeat_count,
        )


class ParallelJoinPolicy(str, Enum):
    ALL = "all"


class ParallelFailurePolicy(str, Enum):
    CANCEL_ALL = "cancel_all"


@dataclass
class ParallelBranch:
    branch_id: str
    items: List['SequenceEntry']

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ParallelBranch':
        return cls(
            branch_id=str(data["branch_id"]),
            items=[sequence_entry_from_dict(item) for item in data.get("items", [])],
        )


@dataclass
class ParallelBlock:
    uuid: str
    branches: List[ParallelBranch]
    join_policy: ParallelJoinPolicy = ParallelJoinPolicy.ALL
    failure_policy: ParallelFailurePolicy = ParallelFailurePolicy.CANCEL_ALL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "parallel",
            "uuid": self.uuid,
            "join_policy": self.join_policy.value,
            "failure_policy": self.failure_policy.value,
            "branches": [branch.to_dict() for branch in self.branches],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ParallelBlock':
        return cls(
            uuid=str(data.get("uuid", uuid4())),
            branches=[
                ParallelBranch.from_dict(branch)
                for branch in data.get("branches", [])
            ],
            join_policy=ParallelJoinPolicy(data.get("join_policy", "all")),
            failure_policy=ParallelFailurePolicy(
                data.get("failure_policy", "cancel_all")
            ),
        )


@dataclass
class SubworkflowBlock:
    """Self-contained workflow snapshot embedded in another workflow."""

    uuid: str
    name: str
    items: List['SequenceEntry']
    source_workflow_id: str = ""
    source_revision: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "subworkflow",
            "uuid": self.uuid,
            "name": self.name,
            "source_workflow_id": self.source_workflow_id,
            "source_revision": self.source_revision,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SubworkflowBlock':
        return cls(
            uuid=str(data["uuid"]),
            name=str(data["name"]),
            source_workflow_id=str(data.get("source_workflow_id", "")),
            source_revision=int(data.get("source_revision", 0)),
            items=[sequence_entry_from_dict(item) for item in data.get("items", [])],
        )


SequenceEntry = Union[SequenceItem, LoopBlock, ParallelBlock, SubworkflowBlock]


def sequence_entry_from_dict(data: Dict[str, Any]) -> SequenceEntry:
    kind = data.get("kind", "action")
    if kind == "loop":
        return LoopBlock.from_dict(data)
    if kind == "parallel":
        return ParallelBlock.from_dict(data)
    if kind == "subworkflow":
        return SubworkflowBlock.from_dict(data)
    return SequenceItem.from_dict(data)
