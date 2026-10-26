"""Resolve the same tool dependency graph for preflight and execution."""

from dataclasses import replace

from ..devices.end_effectors import end_effectors
from ..devices.runtime.runtime import DeviceRuntime
from ..domain.models import ActionType
from .action_control import ActionControlPolicy, ActionParameters


def resolve_tool_policy(
    runtime: DeviceRuntime,
    kind: ActionType,
    parameters: ActionParameters,
    policy: ActionControlPolicy,
) -> ActionControlPolicy:
    routing = end_effectors(runtime)
    tool_kind = None
    if kind is ActionType.MANIPULATE:
        executor = parameters.get("执行器", "快换手")
        tool_kind = {
            "夹爪": "gripper",
            "吸液枪": "pipette",
            "快换手": "tool_changer",
            "右臂转圈注液": "pipette",
        }.get(executor)
    elif kind is ActionType.VISION_CAPTURE:
        target = routing.resolve(
            "gripper",
            arm=str(parameters.get("目标机械臂", "robot1")),
            tool_id=str(parameters.get("tool_id") or ""),
        )
        return replace(
            policy, device_ids=tuple(dict.fromkeys((*policy.device_ids, *target.resources)))
        )
    elif kind is ActionType.CHANGE_GUN and parameters.get("Operation", "取") == "放":
        tool_kind = "pipette"
    if tool_kind is None:
        return policy
    target = routing.from_parameters(tool_kind, parameters)
    return replace(
        policy,
        device_ids=tuple(
            dict.fromkeys(
                (
                    *policy.device_ids,
                    *target.resources,
                )
            )
        ),
    )
