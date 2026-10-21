from dataclasses import replace

import pytest

from src.application.workflow_compiler import WorkflowCompiler, WorkflowCompilationError
from src.domain.execution_plan import ExecutionLoop, ExecutionParallel, ExecutionSubworkflow, iter_execution_steps
from src.domain.models import ActionDefinition, ActionType, LoopBlock, ParallelBlock, ParallelBranch, SequenceItem, SubworkflowBlock
from src.domain.workflow import WorkflowDocument


def item(name, kind=ActionType.WAIT, parameters=None):
    return SequenceItem(name, ActionDefinition(
        name, name, kind, {"wait_seconds": .1} if parameters is None else parameters,
    ))


def document(entries):
    return WorkflowDocument.from_entries(
        workflow_id="start-test", name="Start test", revision=3, entries=entries,
    )


def relocalize(name="locate", arm="left"):
    return item(name, ActionType.VISION_RELOCALIZE, {
        "action_mode": "run", "arm": arm, "station_id": "station-1",
    })


def compensated_move():
    return item("compensated", ActionType.MOVE, {
        "目标": "机械臂", "臂": "左", "模式": "move_l", "点位": [0, 0, 0, 0, 0, 0],
        "补偿": {"mode": "vision", "vision": {"station_id": "station-1"}},
    })


@pytest.mark.parametrize("container", [
    LoopBlock("start", [item("inside")], 3),
    SubworkflowBlock("start", "Subworkflow", [item("inside")]),
    ParallelBlock("start", [ParallelBranch("a", [item("a")]), ParallelBranch("b", [item("b")])]),
])
def test_start_keeps_complete_container_and_following_nodes(container):
    source = document([item("skip"), container, item("last")])
    before = source.to_dict()
    compiled = WorkflowCompiler().compile(source, start_node_id="start")
    assert [entry.uuid for entry in compiled.skipped_entries] == ["skip"]
    assert [entry.uuid for entry in compiled.entries] == ["start", "last"]
    assert isinstance(compiled.plan.root.children[0], (ExecutionLoop, ExecutionSubworkflow, ExecutionParallel))
    assert [identity.runtime_index for identity, _ in iter_execution_steps(compiled.plan)] == list(range(len(compiled.steps)))
    assert "skip" not in [entry.uuid for _, entry in iter_execution_steps(compiled.plan)]
    if isinstance(container, LoopBlock):
        assert [step.loop_iteration for step in compiled.steps] == [1, 2, 3, 0]
    assert source.to_dict() == before


def test_start_uses_stable_node_id_instead_of_action_or_item_identity():
    source = document([item("skip"), item("selected"), item("last")])
    root = replace(source.root, children=(
        source.root.children[0], replace(source.root.children[1], node_id="stable-node"),
        source.root.children[2],
    ))
    compiled = WorkflowCompiler().compile(replace(source, root=root), start_node_id="stable-node")
    assert compiled.steps[0].node_id == "stable-node"
    assert compiled.steps[0].item_uuid == "selected"
    assert compiled.steps[0].runtime_index == 0


@pytest.mark.parametrize("start", ["missing", "inside", ""])
def test_missing_or_nested_start_is_rejected(start):
    with pytest.raises(ValueError, match="顶层节点"):
        WorkflowCompiler().compile(document([LoopBlock("loop", [item("inside")], 2)]), start_node_id=start)


def test_only_remaining_actions_are_validated_and_profile_checked():
    source = document([item("invalid", parameters={"wait_seconds": -1}), item("start")])
    with pytest.raises(WorkflowCompilationError):
        WorkflowCompiler().compile(source)
    assert len(WorkflowCompiler().compile(source, start_node_id="start").steps) == 1
    compiler = WorkflowCompiler(expected_robot_profile_id="tianji-test")
    with pytest.raises(WorkflowCompilationError, match="Robot Profile"):
        compiler.compile(source, start_node_id="start")


def test_skipping_relocalization_is_rejected_before_any_motion():
    source = document([relocalize(), compensated_move()])
    with pytest.raises(ValueError, match="视觉重定位结果"):
        WorkflowCompiler().compile(source, start_node_id="compensated")
    assert len(WorkflowCompiler().compile(source, start_node_id="locate").steps) == 2


def test_relocalization_for_wrong_arm_does_not_supply_compensation():
    source = document([relocalize(arm="right"), compensated_move()])
    with pytest.raises(ValueError, match="视觉重定位结果"):
        WorkflowCompiler().compile(source, start_node_id="locate")


def test_parallel_sibling_cannot_supply_state_but_completed_join_can():
    sibling = ParallelBlock("parallel", [
        ParallelBranch("locate", [relocalize()]),
        ParallelBranch("move", [compensated_move()]),
    ])
    with pytest.raises(ValueError, match="视觉重定位结果"):
        WorkflowCompiler().compile(document([sibling]), start_node_id="parallel")
    joined = ParallelBlock("parallel", [
        ParallelBranch("locate", [relocalize()]), ParallelBranch("other", [item("other")]),
    ])
    compiled = WorkflowCompiler().compile(document([joined, compensated_move()]), start_node_id="parallel")
    assert len(compiled.steps) == 3
