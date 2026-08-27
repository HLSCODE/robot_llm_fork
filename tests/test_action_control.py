from __future__ import annotations

import unittest

from src.domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    ParallelBlock,
    ParallelBranch,
    SequenceItem,
    SequenceItemStatus,
)
from src.domain.execution_plan import ExecutionPlan
from src.domain.execution_context import ExecutionContext
from src.configuration.settings import (
    DeviceSettings,
    ExecutionSettings,
    VisionSettings,
)
from src.devices import (
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
    StopMode,
)
from src.devices.runtime.ids import (
    BALANCE,
    BODY_AXIS,
    CAMERA,
    EXPRESSION_DISPLAY,
    MOBILE_BASE,
    NECK,
    PIPETTE,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
    TOOL_CHANGER,
)
from src.execution import (
    ActionCancellationMode,
    ActionControlPolicy,
    ActionResultCode,
    ActionStopTarget,
    EngineCallbacks,
    ExecutionControl,
    ParallelResourceConflictError,
)
from src.execution.action_control import (
    resolve_base_move_control_policy,
    resolve_change_tool_control_policy,
    resolve_inspect_control_policy,
    resolve_manipulate_control_policy,
    resolve_move_control_policy,
    resolve_trajectory_control_policy,
    resolve_vision_capture_control_policy,
    resolve_vision_relocalization_control_policy,
    resolve_wait_control_policy,
    validate_control_policy_routes,
)
from src.execution.engine import ActionEngine
from src.vision.service import VisionService


def _create_engine(
    runtime: DeviceRuntime,
    *,
    vision_settings: VisionSettings | None = None,
) -> ActionEngine:
    vision = vision_settings or VisionSettings()
    context = ExecutionContext()
    return ActionEngine(
        runtime,
        ExecutionSettings(),
        DeviceSettings(),
        vision,
        lambda **_kwargs: None,
        context,
        VisionService(vision, context),
    )


class ActionControlPolicyTests(unittest.TestCase):
    def test_cooperative_policy_declares_bounded_cancel_latency(self):
        for resolver in (
            resolve_wait_control_policy,
            resolve_inspect_control_policy,
        ):
            policy = resolver({})
            self.assertEqual(
                ActionCancellationMode.BOUNDED_COOPERATIVE,
                policy.cancellation_mode,
            )
            self.assertFalse(policy.blocking_device_call)
            self.assertEqual(
                0.1,
                policy.expected_max_cancel_latency_seconds,
            )
            self.assertFalse(policy.stop_targets)

    def test_hardware_policy_matrix_covers_routed_action_paths(self):
        cases = (
            (
                resolve_move_control_policy,
                {},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM,),
                "robot_system.move_to_pose",
            ),
            (
                resolve_move_control_policy,
                {"目标": "身体"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (BODY_AXIS,),
                "body_axis.move_to",
            ),
            (
                resolve_base_move_control_policy,
                {"move_mode": "position"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (MOBILE_BASE,),
                "mobile_base.move_to_position",
            ),
            (
                resolve_base_move_control_policy,
                {"move_mode": "distance"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (MOBILE_BASE,),
                "mobile_base.move_slowly",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "快换手"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (TOOL_CHANGER,),
                "tool_changer.set_locked",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "继电器"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (RELAY_BANK,),
                "relay.set_channel",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "夹爪"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (ROBOT_SYSTEM,),
                "gripper.execute",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "吸液枪"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (PIPETTE,),
                "pipette.execute",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "颈部"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (NECK,),
                "neck.move",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "表情屏"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (EXPRESSION_DISPLAY,),
                "expression_display.execute",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "智能加粉"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (POWDER_DISPENSER, BALANCE),
                "powder_dispense.run",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "加粉装置"},
                ActionCancellationMode.AFTER_BLOCKING_CALL,
                (POWDER_DISPENSER,),
                "powder_dispenser.execute",
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "右臂转圈注液"},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM, PIPETTE),
                "circle_dispense.execute",
            ),
            (
                resolve_change_tool_control_policy,
                {},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM,),
                "tool_rack.change_tool",
            ),
            (
                resolve_change_tool_control_policy,
                {"Operation": "放"},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM, PIPETTE),
                "tool_rack.change_tool",
            ),
            (
                resolve_vision_capture_control_policy,
                {},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM, CAMERA),
                "vision.capture",
            ),
            (
                resolve_vision_relocalization_control_policy,
                {},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM, CAMERA),
                "vision.relocalize",
            ),
            (
                resolve_trajectory_control_policy,
                {},
                ActionCancellationMode.DEVICE_ASSISTED,
                (ROBOT_SYSTEM,),
                "trajectory.send",
            ),
        )

        for resolver, parameters, mode, device_ids, operation in cases:
            with self.subTest(operation=operation):
                policy = resolver(parameters)
                self.assertEqual(mode, policy.cancellation_mode)
                self.assertEqual(device_ids, policy.device_ids)
                self.assertEqual(operation, policy.operation)
                self.assertTrue(policy.blocking_device_call)
                self.assertIsNone(policy.expected_max_cancel_latency_seconds)

    def test_unknown_route_has_no_false_hardware_cancel_claim(self):
        for resolver, parameters in (
            (resolve_move_control_policy, {"目标": "unknown"}),
            (
                resolve_base_move_control_policy,
                {"move_mode": "unknown"},
            ),
            (
                resolve_manipulate_control_policy,
                {"执行器": "unknown"},
            ),
        ):
            policy = resolver(parameters)
            self.assertEqual(
                ActionCancellationMode.BOUNDED_COOPERATIVE,
                policy.cancellation_mode,
            )
            self.assertFalse(policy.device_ids)
            self.assertFalse(policy.stop_targets)

    def test_expression_executor_aliases_share_one_control_policy(self):
        for executor in (
            "表情屏",
            "表情",
            "expression_display",
            "expression",
        ):
            policy = resolve_manipulate_control_policy({"执行器": executor})
            self.assertEqual(
                (EXPRESSION_DISPLAY,),
                policy.device_ids,
            )
            self.assertEqual(
                "expression_display.execute",
                policy.operation,
            )

    def test_robot_motion_paths_require_both_registered_stop_modes(self):
        policy = resolve_move_control_policy({})

        self.assertTrue(policy.hardware_validation_required)
        self.assertEqual(1, len(policy.stop_targets))
        self.assertEqual(ROBOT_SYSTEM, policy.stop_targets[0].device_id)
        self.assertEqual(
            frozenset({StopMode.QUICK, StopMode.EMERGENCY}),
            policy.stop_targets[0].required_modes,
        )

    def test_control_policy_rejects_contradictory_declaration(self):
        with self.assertRaisesRegex(
            ValueError,
            "must declare cancel latency",
        ):
            ActionControlPolicy(
                operation="invalid",
                cancellation_mode=(ActionCancellationMode.BOUNDED_COOPERATIVE),
                blocking_device_call=False,
            )

        with self.assertRaisesRegex(
            ValueError,
            "must require hardware validation",
        ):
            ActionControlPolicy(
                operation="invalid",
                cancellation_mode=ActionCancellationMode.DEVICE_ASSISTED,
                blocking_device_call=True,
                device_ids=(ROBOT_SYSTEM,),
                stop_targets=(
                    ActionStopTarget(
                        ROBOT_SYSTEM,
                        frozenset({StopMode.QUICK}),
                    ),
                ),
            )

    def test_route_coverage_rejects_missing_and_orphaned_policies(self):
        policy = resolve_wait_control_policy({})

        with self.assertRaisesRegex(
            ValueError,
            "missing policies: new-device",
        ):
            validate_control_policy_routes(
                "test routes",
                frozenset({"existing", "new-device"}),
                {"existing": policy},
            )

        with self.assertRaisesRegex(
            ValueError,
            "orphaned policies: removed-device",
        ):
            validate_control_policy_routes(
                "test routes",
                frozenset({"existing"}),
                {
                    "existing": policy,
                    "removed-device": policy,
                },
            )

    def test_sequence_resources_are_exact_and_include_loop_children(self):
        engine = _create_engine(DeviceRuntime())
        wait = SequenceItem.from_definition(ActionDefinition("wait", "wait", ActionType.WAIT, {}))
        vision = SequenceItem.from_definition(
            ActionDefinition(
                "vision",
                "vision",
                ActionType.VISION_CAPTURE,
                {},
            )
        )
        loop = LoopBlock(
            uuid="loop",
            items=[vision, SequenceItem.from_definition(vision.definition)],
            repeat_count=2,
        )

        self.assertEqual(
            (ROBOT_SYSTEM, CAMERA),
            engine.required_resources(ExecutionPlan.from_entries((wait, loop))),
        )

    def test_parallel_branches_cannot_share_a_device_resource(self):
        engine = _create_engine(DeviceRuntime())
        left = SequenceItem.from_definition(ActionDefinition(
            "left", "left", ActionType.MOVE, {},
        ))
        right = SequenceItem.from_definition(ActionDefinition(
            "right", "right", ActionType.TRAJECTORY, {},
        ))
        parallel = ParallelBlock(
            uuid="parallel-robot",
            branches=[
                ParallelBranch("left", [left]),
                ParallelBranch("right", [right]),
            ],
        )

        with self.assertRaises(ParallelResourceConflictError) as context:
            engine.required_resources(ExecutionPlan.from_entries((parallel,)))

        self.assertEqual(ROBOT_SYSTEM, context.exception.resource_id)
        self.assertEqual(("left", "right"), context.exception.branch_ids)

class ActionControlPreflightTests(unittest.TestCase):
    def test_engine_rejects_stop_capability_mismatch_before_device_init(self):
        runtime = DeviceRuntime()
        initialized = False

        def factory() -> object:
            nonlocal initialized
            initialized = True
            return object()

        runtime.register(
            DeviceRegistration(
                device_id=ROBOT_SYSTEM,
                capabilities=frozenset(
                    {
                        DeviceCapability.MOTION,
                        DeviceCapability.ARM_MOTION,
                    }
                ),
                factory=factory,
                close=lambda _device: None,
            )
        )
        engine = _create_engine(runtime)
        item = SequenceItem.from_definition(
            ActionDefinition(
                id="unsafe-move",
                name="unsafe move",
                type=ActionType.MOVE,
                parameters={},
            )
        )
        started_policies: list[ActionControlPolicy] = []
        failures = []
        callbacks = EngineCallbacks(
            on_step_started=(lambda _index, _item, policy: started_policies.append(policy)),
            on_step_completed=lambda _index, _item: None,
            on_step_failed=(lambda _index, _item, failure: failures.append(failure)),
            on_loop_progress=lambda _uuid, _current, _total: None,
            on_parallel_branch=(
                lambda _parallel, _branch, _state, _error: None
            ),
            on_log=lambda _message, _level: None,
        )

        result = engine.run(
            ExecutionPlan.from_entries((item,)),
            ExecutionControl(),
            callbacks,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            ActionResultCode.CONTROL_POLICY_MISMATCH.value,
            result.error_code,
        )
        self.assertEqual(
            "action.control_policy.validate",
            result.error_operation,
        )
        self.assertEqual(ROBOT_SYSTEM, result.error_device_id)
        self.assertIn("缺少 emergency, quick", result.error or "")
        self.assertIn("不能通过关闭安全预检继续执行", result.error or "")
        self.assertFalse(initialized)
        self.assertEqual(
            ActionCancellationMode.DEVICE_ASSISTED,
            started_policies[0].cancellation_mode,
        )
        self.assertEqual(
            ActionResultCode.CONTROL_POLICY_MISMATCH,
            failures[0].code,
        )


class ActionParallelExecutionTests(unittest.TestCase):
    def test_parallel_wait_branches_join_and_report_stable_branch_states(self):
        engine = _create_engine(DeviceRuntime())
        parallel = ParallelBlock(
            uuid="parallel-waits",
            branches=[
                ParallelBranch("left", [_wait_item("left", 0.01)]),
                ParallelBranch("right", [_wait_item("right", 0.01)]),
            ],
        )
        branch_states: list[tuple[str, str, str]] = []
        step_paths: list[str] = []

        result = engine.run(
            ExecutionPlan.from_entries((parallel,)),
            ExecutionControl(),
            _callbacks(branch_states, step_paths),
        )

        self.assertTrue(result.success)
        self.assertEqual(
            {
                ("parallel-waits", "left", "started"),
                ("parallel-waits", "left", "completed"),
                ("parallel-waits", "right", "started"),
                ("parallel-waits", "right", "completed"),
            },
            set(branch_states),
        )
        self.assertEqual(2, len(step_paths))
        self.assertTrue(any("/branch/left/" in path for path in step_paths))
        self.assertTrue(any("/branch/right/" in path for path in step_paths))

    def test_parallel_failure_cancels_and_joins_sibling_branch(self):
        engine = _create_engine(DeviceRuntime())
        failed = _wait_item("failed", "not-a-number")
        sibling = _wait_item("sibling", 2.0)
        parallel = ParallelBlock(
            uuid="parallel-failure",
            branches=[
                ParallelBranch("failed", [failed]),
                ParallelBranch("sibling", [sibling]),
            ],
        )
        branch_states: list[tuple[str, str, str]] = []

        result = engine.run(
            ExecutionPlan.from_entries((parallel,)),
            ExecutionControl(),
            _callbacks(branch_states, []),
        )

        self.assertFalse(result.success)
        self.assertFalse(result.cancelled)
        self.assertEqual(ActionResultCode.INVALID_PARAMETERS.value, result.error_code)
        self.assertIn(
            ("parallel-failure", "failed", "failed"),
            branch_states,
        )
        self.assertIn(
            ("parallel-failure", "sibling", "cancelled"),
            branch_states,
        )
        self.assertEqual(SequenceItemStatus.PENDING, sibling.status)


def _wait_item(item_id: str, wait_seconds: object) -> SequenceItem:
    return SequenceItem.from_definition(ActionDefinition(
        item_id,
        item_id,
        ActionType.WAIT,
        {"wait_seconds": wait_seconds},
    ))


def _callbacks(
    branch_states: list[tuple[str, str, str]],
    step_paths: list[str],
) -> EngineCallbacks:
    return EngineCallbacks(
        on_step_started=(
            lambda identity, _item, _policy: step_paths.append(identity.path)
        ),
        on_step_completed=lambda _identity, _item: None,
        on_step_failed=lambda _identity, _item, _failure: None,
        on_loop_progress=lambda _uuid, _current, _total: None,
        on_parallel_branch=(
            lambda parallel, branch, state, _error: branch_states.append(
                (parallel, branch, state)
            )
        ),
        on_log=lambda _message, _level: None,
    )


if __name__ == "__main__":
    unittest.main()
