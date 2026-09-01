"""Normalization used only while migrating legacy action catalogs."""

from __future__ import annotations

from copy import deepcopy

from ..domain.models import ActionDefinition, ActionType
from ..geometry.pose_compensation import parse_pose


def normalize_legacy_action(action: ActionDefinition) -> ActionDefinition:
    """Return an action whose legacy textual pose uses the canonical JSON form."""
    parameters = deepcopy(action.parameters)
    if action.type is ActionType.MOVE and "点位" in parameters:
        parameters["点位"] = parse_pose(parameters["点位"])
    return ActionDefinition(
        id=action.id,
        name=action.name,
        type=action.type,
        parameters=parameters,
        robot_profile_id=action.robot_profile_id,
    )
