"""Check per-run vision state before executing a workflow suffix."""

from ..domain.arm_names import normalize_arm_name
from ..domain.execution_plan import (
    ExecutionAction, ExecutionLoop, ExecutionPlan, ExecutionSequence,
    ExecutionSubworkflow,
)
from ..domain.models import ActionType


def validate_start_dependencies(plan: ExecutionPlan) -> None:
    """Require vision compensation state to be produced in this fresh run.

    Parallel siblings cannot supply each other's inputs: their execution order
    is undefined. Completed branches may supply state after the all-join.
    Physical prerequisites (a held object or mounted tool) cannot be inferred.
    """
    _check_sequence(plan.root, set())


def _check_sequence(
    sequence: ExecutionSequence, available: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    available = set(available)
    for node in sequence.children:
        if isinstance(node, ExecutionAction):
            _check_action(node, available)
        elif isinstance(node, (ExecutionLoop, ExecutionSubworkflow)):
            available = _check_sequence(node.body, available)
        else:
            produced = set(available)
            for branch in node.branches:
                produced.update(_check_sequence(branch.body, available))
            available = produced
    return available


def _check_action(node: ExecutionAction, available: set[tuple[str, str]]) -> None:
    # The compiler has already validated and defaulted these canonical fields.
    definition = node.item.definition
    parameters = definition.parameters
    if definition.type is ActionType.VISION_RELOCALIZE:
        station = str(parameters.get("station_id") or parameters.get("station_name") or "").strip()
        arm = normalize_arm_name(parameters.get("arm"))
        if station:
            available.add((station, arm))
        return
    if definition.type is not ActionType.MOVE:
        return
    if parameters.get("模式") == "move_joints" or parameters.get("目标") == "机械臂相对":
        return
    compensation = parameters.get("补偿") or {}
    if compensation.get("mode") != "vision":
        return
    station = str(compensation["vision"]["station_id"]).strip()
    arm = normalize_arm_name(parameters.get("臂"))
    if (station, arm) not in available:
        arm_label = "左臂" if arm == "left" else "右臂"
        raise ValueError(
            f"动作“{definition.name}”依赖工位“{station}”（{arm_label}）的视觉重定位结果；"
            "请从该重定位节点或更早节点开始。本次执行不会沿用上次运行的结果。"
        )
