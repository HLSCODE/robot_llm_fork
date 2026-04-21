from enum import Enum
from dataclasses import dataclass, asdict
from typing import Any, Dict
from uuid import uuid4
import json


class ActionType(Enum):
    MOVE = "MOVE_TO_POINT"
    MANIPULATE = "ARM_ACTION"
    INSPECT = "INSPECT_AND_OUTPUT"
    WAIT = "WAIT"
    CHANGE_GUN = "CHANGE_GUN"
    VISION_CAPTURE = "VISION_CAPTURE"



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
