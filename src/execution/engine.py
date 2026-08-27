"""Synchronous action engine used exclusively by ExecutionManager."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Any

from ..domain.execution_context import ExecutionContext
from ..domain.robot_profile import normalize_robot_profile_id
from ..domain.models import (
    ActionType,
    SequenceItem,
    SequenceItemStatus,
)
from ..domain.execution_plan import (
    ExecutionAction,
    ExecutionLoop,
    ExecutionNode,
    ExecutionParallel,
    ExecutionPlan,
    ExecutionSequence,
    ExecutionStepIdentity,
    ExecutionSubworkflow,
    iter_execution_steps,
)
from ..devices import DeviceNotRegisteredError, DeviceRuntime
from ..configuration.settings import (
    DeviceSettings,
    ExecutionSettings,
    VisionSettings,
)
from .action_control import (
    BASE_MOVE_CONTROL_POLICIES,
    MANIPULATE_CONTROL_POLICIES,
    MOVE_CONTROL_POLICIES,
    ActionControlPolicy,
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
from .handler_api import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionResultCode,
    ActionTimeoutError,
)
from .handler_registry import ActionHandlerRegistry
from .control import ExecutionControl
from .handlers import (
    BaseMoveActionHandler,
    BodyMoveActionHandler,
    ChangeToolActionHandler,
    InspectActionHandler,
    ManipulationHandlerOptions,
    MotionHandlerOptions,
    MoveActionHandler,
    RobotMoveActionHandler,
    TrajectoryActionHandler,
    TrajectoryHandlerOptions,
    VisionCaptureActionHandler,
    VisionRelocalizationActionHandler,
    WaitActionHandler,
    create_manipulation_handler,
)
from .manager import EngineCallbacks
from .models import EngineResult, ParallelResourceConflictError
from ..vision.service import VisionService

logger = logging.getLogger(__name__)


class ActionEngine:
    """Execute one action sequence synchronously against DeviceRuntime."""

    _ACTION_OPERATION = "action.execute"
    _RUN_STEP_OPERATION = "action_engine.run_step"
    _TIMEOUT_CONFIGURATION_OPERATION = "action.configure_timeout"
    _CONTROL_POLICY_OPERATION = "action.control_policy.validate"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        execution_settings: ExecutionSettings,
        device_settings: DeviceSettings,
        vision_settings: VisionSettings,
        external_localization_reader: Callable[..., dict[str, Any] | None],
        execution_context: ExecutionContext,
        vision_service: VisionService,
        *,
        robot_profile_id: str = "unscoped",
    ) -> None:
        self._device_runtime = device_runtime
        self.execution_context = execution_context
        self._vision_service = vision_service
        self._robot_profile_id = normalize_robot_profile_id(robot_profile_id)
        self._callbacks: EngineCallbacks | None = None
        self._default_action_timeout_seconds = execution_settings.execution_action_timeout_seconds
        if self._default_action_timeout_seconds <= 0:
            raise ValueError("EXECUTION_ACTION_TIMEOUT_SECONDS must be positive")
        self._motion_handler_options = MotionHandlerOptions(
            arm_move_max_attempts=int(execution_settings.execution_arm_move_max_attempts),
            arm_move_retry_delay_seconds=float(
                execution_settings.execution_arm_move_retry_delay_seconds
            ),
            body_poll_interval_seconds=float(
                execution_settings.execution_body_poll_interval_seconds
            ),
        )
        self._manipulation_handler_options = ManipulationHandlerOptions(
            gripper_max_attempts=int(execution_settings.execution_gripper_max_attempts),
            gripper_retry_delay_seconds=float(
                execution_settings.execution_gripper_retry_delay_seconds
            ),
        )
        self._trajectory_handler_options = TrajectoryHandlerOptions(
            poll_interval_seconds=float(
                execution_settings.execution_trajectory_poll_interval_seconds
            )
        )
        self._tapping_config_provider = device_settings.tapping_config
        self._vision_settings = vision_settings
        self._external_localization_reader = external_localization_reader
        self._handler_registry = self._create_handler_registry()

    def _create_handler_registry(self) -> ActionHandlerRegistry:
        registry = ActionHandlerRegistry()
        move_handler = MoveActionHandler(
            RobotMoveActionHandler(
                self._device_runtime,
                self.execution_context,
                self._motion_handler_options,
                self._vision_settings,
                self._external_localization_reader,
            ),
            BodyMoveActionHandler(
                self._device_runtime,
                self._motion_handler_options,
            ),
        )
        validate_control_policy_routes(
            "move targets",
            move_handler.registered_targets,
            MOVE_CONTROL_POLICIES,
        )
        registry.register(
            ActionType.MOVE,
            move_handler,
            resolve_move_control_policy,
        )

        base_move_handler = BaseMoveActionHandler(self._device_runtime)
        validate_control_policy_routes(
            "base move modes",
            base_move_handler.registered_modes,
            BASE_MOVE_CONTROL_POLICIES,
        )
        registry.register(
            ActionType.BASE_MOVE,
            base_move_handler,
            resolve_base_move_control_policy,
        )

        manipulation_handler = create_manipulation_handler(
            self._device_runtime,
            self._manipulation_handler_options,
            self._tapping_config_provider,
        )
        validate_control_policy_routes(
            "manipulation executors",
            manipulation_handler.registered_executors,
            MANIPULATE_CONTROL_POLICIES,
        )
        registry.register(
            ActionType.MANIPULATE,
            manipulation_handler,
            resolve_manipulate_control_policy,
        )
        registry.register(
            ActionType.INSPECT,
            InspectActionHandler(),
            resolve_inspect_control_policy,
        )
        registry.register(
            ActionType.WAIT,
            WaitActionHandler(),
            resolve_wait_control_policy,
        )
        registry.register(
            ActionType.CHANGE_GUN,
            ChangeToolActionHandler(self._device_runtime),
            resolve_change_tool_control_policy,
        )
        registry.register(
            ActionType.VISION_CAPTURE,
            VisionCaptureActionHandler(
                self._device_runtime,
                self._vision_service,
            ),
            resolve_vision_capture_control_policy,
        )
        registry.register(
            ActionType.VISION_RELOCALIZE,
            VisionRelocalizationActionHandler(
                self._device_runtime,
                self._vision_service,
            ),
            resolve_vision_relocalization_control_policy,
        )
        registry.register(
            ActionType.TRAJECTORY,
            TrajectoryActionHandler(
                self._device_runtime,
                self._trajectory_handler_options,
            ),
            resolve_trajectory_control_policy,
        )
        registry.validate_complete()
        return registry

    def run(
        self,
        plan: ExecutionPlan,
        control: ExecutionControl,
        callbacks: EngineCallbacks,
    ) -> EngineResult:
        """Execute one structured plan in the manager-owned worker."""
        self._require_plan_profile(plan)
        self._callbacks = callbacks
        self.execution_context.clear()
        identities = {
            identity.path: identity
            for identity, _item in iter_execution_steps(plan)
        }
        try:
            return self._execute_sequence(
                plan.root,
                control,
                identities,
                path="root",
            )
        finally:
            self._callbacks = None

    def required_resources(
        self,
        plan: ExecutionPlan,
    ) -> tuple[str, ...]:
        """Resolve leases and reject conflicting parallel branches."""
        self._require_plan_profile(plan)
        return self._node_resources(plan.root)

    def _require_plan_profile(self, plan: ExecutionPlan) -> None:
        for _identity, item in iter_execution_steps(plan):
            declared = item.definition.robot_profile_id
            if declared != self._robot_profile_id:
                raise ValueError(
                    "execution action belongs to Robot Profile "
                    f"{declared!r}; active profile is {self._robot_profile_id!r}"
                )

    def _execute_sequence(
        self,
        sequence: ExecutionSequence,
        control: ExecutionControl,
        identities: dict[str, ExecutionStepIdentity],
        *,
        path: str,
    ) -> EngineResult:
        for index, node in enumerate(sequence.children):
            if not control.wait_if_paused():
                return EngineResult(success=False, cancelled=True)
            result = self._execute_node(
                node,
                control,
                identities,
                path=f"{path}/{index}",
            )
            if not result.success:
                return result
        return EngineResult(success=True)

    def _execute_node(
        self,
        node: ExecutionNode,
        control: ExecutionControl,
        identities: dict[str, ExecutionStepIdentity],
        *,
        path: str,
    ) -> EngineResult:
        if isinstance(node, ExecutionAction):
            return self._execute_plan_action(node.item, control, identities[path])
        if isinstance(node, ExecutionLoop):
            for iteration in range(1, node.repeat_count + 1):
                self._on_loop_progress(node.loop_id, iteration, node.repeat_count)
                result = self._execute_sequence(
                    node.body,
                    control,
                    identities,
                    path=f"{path}/iteration/{iteration}",
                )
                if not result.success:
                    return result
            return EngineResult(success=True)
        if isinstance(node, ExecutionSubworkflow):
            return self._execute_sequence(
                node.body,
                control,
                identities,
                path=f"{path}/subworkflow/{node.subworkflow_id}",
            )
        return self._execute_parallel(node, control, identities, path=path)

    def _execute_plan_action(
        self,
        item: SequenceItem,
        control: ExecutionControl,
        identity: ExecutionStepIdentity,
    ) -> EngineResult:
        if control.cancel_requested:
            return EngineResult(success=False, cancelled=True)
        try:
            policy = self._handler_registry.control_policy(
                item.definition.type,
                item.definition.parameters,
            )
            item.status = SequenceItemStatus.RUNNING
            self._on_step_started(identity, item, policy)
            result = self._execute_action(item, control, policy)
            if result.successful:
                item.status = SequenceItemStatus.SUCCESS
                self._on_step_completed(identity, item)
                return EngineResult(success=True)
            if self._reset_if_cancelled(control, item):
                return EngineResult(success=False, cancelled=True)
            item.status = SequenceItemStatus.FAILED
            self._on_step_failed(identity, item, result)
            return _engine_failure(result)
        except ActionCancelledError:
            item.status = SequenceItemStatus.PENDING
            return EngineResult(success=False, cancelled=True)
        except Exception as exc:
            item.status = SequenceItemStatus.FAILED
            failure = ActionHandlerResult.failed(
                ActionResultCode.INTERNAL_ERROR,
                f"执行异常: {exc}",
                operation=self._RUN_STEP_OPERATION,
            )
            self._on_step_failed(identity, item, failure)
            return _engine_failure(failure)

    def _execute_parallel(
        self,
        node: ExecutionParallel,
        control: ExecutionControl,
        identities: dict[str, ExecutionStepIdentity],
        *,
        path: str,
    ) -> EngineResult:
        branch_controls = {
            branch.branch_id: control.child()
            for branch in node.branches
        }
        results: dict[str, EngineResult] = {}
        with ThreadPoolExecutor(
            max_workers=len(node.branches),
            thread_name_prefix=f"ExecutionBranch-{node.parallel_id[:8]}",
        ) as executor:
            futures = {}
            for branch in node.branches:
                self._on_parallel_branch(node.parallel_id, branch.branch_id, "started")
                future = executor.submit(
                    self._execute_sequence,
                    branch.body,
                    branch_controls[branch.branch_id],
                    identities,
                    path=f"{path}/branch/{branch.branch_id}",
                )
                futures[future] = branch.branch_id
            for future in as_completed(futures):
                branch_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = EngineResult(
                        success=False,
                        error=f"并行分支执行异常: {exc}",
                        error_code=ActionResultCode.INTERNAL_ERROR.value,
                        error_operation="execution.parallel.branch",
                    )
                results[branch_id] = result
                state = "completed" if result.success else (
                    "cancelled" if result.cancelled else "failed"
                )
                self._on_parallel_branch(
                    node.parallel_id,
                    branch_id,
                    state,
                    result.error,
                )
                if not result.success and not result.cancelled:
                    for other_id, branch_control in branch_controls.items():
                        if other_id != branch_id:
                            branch_control.cancel()
        if control.cancel_requested:
            return EngineResult(success=False, cancelled=True)
        failures = [
            results[branch.branch_id]
            for branch in node.branches
            if not results[branch.branch_id].success
            and not results[branch.branch_id].cancelled
        ]
        return failures[0] if failures else EngineResult(success=True)

    def _node_resources(
        self,
        node: ExecutionSequence | ExecutionNode,
    ) -> tuple[str, ...]:
        if isinstance(node, ExecutionAction):
            return tuple(dict.fromkeys(self._handler_registry.control_policy(
                node.item.definition.type,
                node.item.definition.parameters,
            ).device_ids))
        if isinstance(node, ExecutionSequence):
            resources: list[str] = []
            for child in node.children:
                for resource_id in self._node_resources(child):
                    if resource_id not in resources:
                        resources.append(resource_id)
            return tuple(resources)
        if isinstance(node, ExecutionLoop):
            return self._node_resources(node.body)
        if isinstance(node, ExecutionSubworkflow):
            return self._node_resources(node.body)
        branch_resources = [
            self._node_resources(branch.body)
            for branch in node.branches
        ]
        for left_index, left in enumerate(branch_resources):
            for right_index in range(left_index + 1, len(branch_resources)):
                overlap = set(left) & set(branch_resources[right_index])
                if overlap:
                    raise ParallelResourceConflictError(
                        node.parallel_id,
                        sorted(overlap)[0],
                        (
                            node.branches[left_index].branch_id,
                            node.branches[right_index].branch_id,
                        ),
                    )
        merged_resources: list[str] = []
        for branch in branch_resources:
            for resource_id in branch:
                if resource_id not in merged_resources:
                    merged_resources.append(resource_id)
        return tuple(merged_resources)

    def _required_callbacks(self) -> EngineCallbacks:
        if self._callbacks is None:
            raise RuntimeError("action engine callbacks are unavailable")
        return self._callbacks

    def _on_step_started(
        self,
        identity: ExecutionStepIdentity,
        item: SequenceItem,
        control_policy: ActionControlPolicy,
    ) -> None:
        self._required_callbacks().on_step_started(
            identity,
            item,
            control_policy,
        )

    def _on_step_completed(
        self,
        identity: ExecutionStepIdentity,
        item: SequenceItem,
    ) -> None:
        self._required_callbacks().on_step_completed(identity, item)

    def _on_step_failed(
        self,
        identity: ExecutionStepIdentity,
        item: SequenceItem,
        failure: ActionHandlerResult,
    ) -> None:
        self._required_callbacks().on_step_failed(identity, item, failure)

    def _on_loop_progress(
        self,
        loop_uuid: str,
        current_iteration: int,
        total_iterations: int,
    ) -> None:
        self._required_callbacks().on_loop_progress(
            loop_uuid,
            current_iteration,
            total_iterations,
        )

    def _on_parallel_branch(
        self,
        parallel_id: str,
        branch_id: str,
        state: str,
        error: str = "",
    ) -> None:
        self._required_callbacks().on_parallel_branch(
            parallel_id,
            branch_id,
            state,
            error,
        )

    def _on_log(self, message: str, level: str = "info") -> None:
        self._required_callbacks().on_log(message, level)

    @staticmethod
    def _reset_cancelled_item(item: SequenceItem) -> None:
        item.status = SequenceItemStatus.PENDING

    @classmethod
    def _reset_if_cancelled(
        cls,
        control: ExecutionControl,
        item: SequenceItem,
    ) -> bool:
        if not control.cancel_requested:
            return False
        cls._reset_cancelled_item(item)
        return True

    # ------------------------------------------------------------------
    # 动作分发（与 execution.py 逻辑一致）
    # ------------------------------------------------------------------

    def _execute_action(
        self,
        item: SequenceItem,
        control: ExecutionControl,
        control_policy: ActionControlPolicy,
    ) -> ActionHandlerResult:
        definition = item.definition
        params = definition.parameters

        self._on_log(f"正在执行: {definition.name}")
        self._on_log(f"参数: {params}")

        try:
            timeout_seconds = float(
                params.get(
                    "timeout_seconds",
                    self._default_action_timeout_seconds,
                )
            )
            context = ActionExecutionContext(
                action_name=definition.name,
                control=control,
                timeout_seconds=timeout_seconds,
                log=self._on_log,
            )
        except (TypeError, ValueError) as exc:
            message = f"动作超时配置无效: {exc}"
            self._on_log(message, "error")
            return ActionHandlerResult.failed(
                ActionResultCode.INVALID_PARAMETERS,
                message,
                operation=self._TIMEOUT_CONFIGURATION_OPERATION,
            )

        policy_failure = self._validate_control_policy(control_policy)
        if policy_failure is not None:
            self._on_log(policy_failure.message, "error")
            return policy_failure

        try:
            return self._handler_registry.execute(
                definition.type,
                params,
                context,
            )
        except ActionCancelledError:
            self._on_log(f"动作已取消: {definition.name}")
            raise
        except ActionTimeoutError as exc:
            message = str(exc)
            self._on_log(message, "error")
            return ActionHandlerResult.failed(
                ActionResultCode.ACTION_TIMEOUT,
                message,
                operation=self._ACTION_OPERATION,
            )
        except Exception as exc:
            message = f"执行错误: {exc}"
            self._on_log(message, "error")
            return ActionHandlerResult.failed(
                ActionResultCode.INTERNAL_ERROR,
                message,
                operation=self._ACTION_OPERATION,
            )

    def _validate_control_policy(
        self,
        policy: ActionControlPolicy,
    ) -> ActionHandlerResult | None:
        for target in policy.stop_targets:
            try:
                declared_modes = self._device_runtime.declared_stop_modes(target.device_id)
            except DeviceNotRegisteredError as exc:
                return ActionHandlerResult.failed(
                    ActionResultCode.CONTROL_POLICY_MISMATCH,
                    f"动作控制策略引用了未注册的停止设备: {target.device_id}: {exc}",
                    operation=self._CONTROL_POLICY_OPERATION,
                    device_id=target.device_id,
                )

            missing_modes = target.required_modes - declared_modes
            if not missing_modes:
                continue
            missing_values = ", ".join(
                mode.value
                for mode in sorted(
                    missing_modes,
                    key=lambda item: item.value,
                )
            )
            return ActionHandlerResult.failed(
                ActionResultCode.CONTROL_POLICY_MISMATCH,
                (
                    "设备停止能力不满足动作控制策略: "
                    f"{target.device_id} 缺少 {missing_values}；"
                    "当前机器人 Provider 必须通过公共驱动接口实现并声明这些停止能力，"
                    "不能通过关闭安全预检继续执行"
                ),
                operation=self._CONTROL_POLICY_OPERATION,
                device_id=target.device_id,
            )
        return None


def _engine_failure(failure: ActionHandlerResult) -> EngineResult:
    return EngineResult(
        success=False,
        error=failure.message,
        error_code=failure.code.value,
        error_operation=failure.operation,
        error_device_id=failure.device_id,
        error_category=failure.error_category,
        raw_error_code=failure.raw_error_code,
    )
