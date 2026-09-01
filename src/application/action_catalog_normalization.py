"""Normalization used only while migrating legacy action catalogs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from ..domain.models import ActionDefinition, ActionType
from ..geometry.pose_compensation import parse_pose


def normalize_legacy_action(action: ActionDefinition) -> ActionDefinition:
    """Return one legacy action using only canonical parameter representations."""
    parameters = deepcopy(action.parameters)
    if action.type is ActionType.MOVE and "点位" in parameters:
        pose = parameters["点位"]
        if isinstance(pose, str):
            pose = pose.strip().removeprefix("[200~")
        parameters["点位"] = parse_pose(pose)
    if action.type is ActionType.VISION_RELOCALIZE:
        _flatten_legacy_marker(parameters)
    return ActionDefinition(
        id=action.id,
        name=action.name,
        type=action.type,
        parameters=parameters,
        robot_profile_id=action.robot_profile_id,
    )


def _flatten_legacy_marker(parameters: dict[str, object]) -> None:
    marker = parameters.pop("marker", None)
    if marker is None:
        return
    if not isinstance(marker, Mapping):
        raise TypeError("legacy vision relocalization marker must be an object")

    dimensions = {
        "marker_width": marker.get("width", marker.get("w")),
        "marker_height": marker.get("height", marker.get("h")),
    }
    for field, value in dimensions.items():
        if field in parameters or value in (None, ""):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError(f"legacy vision relocalization {field} must be numeric")
        parameters[field] = float(value)
