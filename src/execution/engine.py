"""Synchronous action engine used exclusively by ExecutionManager."""

from collections.abc import Sequence
import logging
from typing import Any

from ..core.execution_context import ExecutionContext
from ..core.models import (
    ActionType,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ..device_runtime import DeviceNotRegisteredError, DeviceRuntime
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
from .action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionHandlerRegistry,
    ActionResultCode,
    ActionTimeoutError,
    InspectActionHandler,
    WaitActionHandler,
)
from .control import ExecutionControl
from .handlers import (
    BaseMoveActionHandler,
    BodyMoveActionHandler,
    ChangeToolActionHandler,
    ManipulationHandlerOptions,
    MotionHandlerOptions,
    MoveActionHandler,
    RobotMoveActionHandler,
    TrajectoryActionHandler,
    TrajectoryHandlerOptions,
    VisionCaptureActionHandler,
    VisionRelocalizationActionHandler,
    create_manipulation_handler,
)
from .manager import EngineCallbacks
from .models import EngineResult

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
        config: Any,
    ) -> None:
        self._device_runtime = device_runtime
        self.execution_context = ExecutionContext()
        self._callbacks: EngineCallbacks | None = None
        self._default_action_timeout_seconds = float(
            getattr(config, "EXECUTION_ACTION_TIMEOUT_SECONDS", 600.0)
        )
        if self._default_action_timeout_seconds <= 0:
            raise ValueError(
                "EXECUTION_ACTION_TIMEOUT_SECONDS must be positive"
            )
        self._motion_handler_options = MotionHandlerOptions(
            arm_move_max_attempts=int(
                getattr(
                    config,
                    "EXECUTION_ARM_MOVE_MAX_ATTEMPTS",
                    3,
                )
            ),
            arm_move_retry_delay_seconds=float(
                getattr(
                    config,
                    "EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS",
                    0.5,
                )
            ),
            body_poll_interval_seconds=float(
                getattr(
                    config,
                    "EXECUTION_BODY_POLL_INTERVAL_SECONDS",
                    0.1,
                )
            ),
        )
        self._manipulation_handler_options = ManipulationHandlerOptions(
            gripper_max_attempts=int(
                getattr(
                    config,
                    "EXECUTION_GRIPPER_MAX_ATTEMPTS",
                    3,
                )
            ),
            gripper_retry_delay_seconds=float(
                getattr(
                    config,
                    "EXECUTION_GRIPPER_RETRY_DELAY_SECONDS",
                    0.5,
                )
            ),
        )
        self._trajectory_handler_options = TrajectoryHandlerOptions(
            poll_interval_seconds=float(
                getattr(
                    config,
                    "EXECUTION_TRAJECTORY_POLL_INTERVAL_SECONDS",
                    0.5,
                )
            )
        )
        tapping_config_getter = getattr(
            config,
            "get_tapping_config",
            None,
        )
        self._tapping_config_provider = (
            tapping_config_getter
            if callable(tapping_config_getter)
            else lambda: {}
        )
        self._handler_registry = self._create_handler_registry()

    def _create_handler_registry(self) -> ActionHandlerRegistry:
        registry = ActionHandlerRegistry()
        move_handler = MoveActionHandler(
            RobotMoveActionHandler(
                self._device_runtime,
                self.execution_context,
                self._motion_handler_options,
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
            VisionCaptureActionHandler(self._device_runtime),
            resolve_vision_capture_control_policy,
        )
        registry.register(
            ActionType.VISION_RELOCALIZE,
            VisionRelocalizationActionHandler(
                self._device_runtime,
                self.execution_context,
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
        sequence: Sequence[SequenceEntry],
        control: ExecutionControl,
        callbacks: EngineCallbacks,
    ) -> EngineResult:
        """Execute a sequence in the current worker thread."""
        self._callbacks = callbacks
        self.execution_context.clear()
        failure: ActionHandlerResult | None = None
        try:
            flat_sequence: list[tuple[SequenceItem, LoopBlock | None]] = []
            for entry in sequence:
                if isinstance(entry, LoopBlock):
                    for _ in range(entry.repeat_count):
                        for child in entry.items:
                            clone = SequenceItem.from_dict(child.to_dict())
                            clone.uuid = child.uuid
                            flat_sequence.append((clone, entry))
                elif isinstance(entry, SequenceItem):
                    flat_sequence.append((entry, None))

            loop_iteration: dict[str, int] = {}
            loop_item_counter: dict[str, int] = {}

            for index, (item, loop) in enumerate(flat_sequence):
                if control.cancel_requested:
                    self._on_log("执行已停止")
                    return EngineResult(success=False, cancelled=True)
                if not control.wait_if_paused():
                    self._on_log("执行已停止")
                    return EngineResult(success=False, cancelled=True)

                if loop is not None:
                    counter = loop_item_counter.get(loop.uuid, 0)
                    iter_size = len(loop.items)
                    if counter == 0:
                        current_iter = loop_iteration.get(loop.uuid, 0) + 1
                        loop_iteration[loop.uuid] = current_iter
                        self._on_log(f"🔁 循环块 第 {current_iter}/{loop.repeat_count} 轮开始")
                        self._on_loop_progress(loop.uuid, current_iter, loop.repeat_count)
                    loop_item_counter[loop.uuid] = (counter + 1) % iter_size

                try:
                    control_policy = self._handler_registry.control_policy(
                        item.definition.type,
                        item.definition.parameters,
                    )
                    item.status = SequenceItemStatus.RUNNING
                    self._on_step_started(index, item, control_policy)
                    result = self._execute_action(
                        item,
                        control,
                        control_policy,
                    )
                    if result.successful:
                        item.status = SequenceItemStatus.SUCCESS
                        self._on_step_completed(index, item)
                    elif control.cancel_requested:
                        item.status = SequenceItemStatus.PENDING
                        return EngineResult(success=False, cancelled=True)
                    else:
                        item.status = SequenceItemStatus.FAILED
                        failure = result
                        self._on_step_failed(index, item, result)
                        break
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
                    self._on_step_failed(index, item, failure)
                    break
        finally:
            self._callbacks = None

        if control.cancel_requested:
            return EngineResult(success=False, cancelled=True)
        if failure is not None:
            return EngineResult(
                success=False,
                error=failure.message,
                error_code=failure.code.value,
                error_operation=failure.operation,
                error_device_id=failure.device_id,
            )
        return EngineResult(success=True)

    def required_resources(
        self,
        sequence: Sequence[SequenceEntry],
    ) -> tuple[str, ...]:
        """Resolve the exact device lease set before accepting a run."""
        resources: list[str] = []
        seen: set[str] = set()
        for entry in sequence:
            items = entry.items if isinstance(entry, LoopBlock) else (entry,)
            for item in items:
                if not isinstance(item, SequenceItem):
                    continue
                policy = self._handler_registry.control_policy(
                    item.definition.type,
                    item.definition.parameters,
                )
                for device_id in policy.device_ids:
                    if device_id in seen:
                        continue
                    seen.add(device_id)
                    resources.append(device_id)
        return tuple(resources)

    def _required_callbacks(self) -> EngineCallbacks:
        if self._callbacks is None:
            raise RuntimeError("action engine callbacks are unavailable")
        return self._callbacks

    def _on_step_started(
        self,
        index: int,
        item: SequenceItem,
        control_policy: ActionControlPolicy,
    ) -> None:
        self._required_callbacks().on_step_started(
            index,
            item,
            control_policy,
        )

    def _on_step_completed(self, index: int, item: SequenceItem) -> None:
        self._required_callbacks().on_step_completed(index, item)

    def _on_step_failed(
        self,
        index: int,
        item: SequenceItem,
        failure: ActionHandlerResult,
    ) -> None:
        self._required_callbacks().on_step_failed(index, item, failure)

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

    def _on_log(self, message: str, level: str = "info") -> None:
        self._required_callbacks().on_log(message, level)

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
                declared_modes = self._device_runtime.declared_stop_modes(
                    target.device_id
                )
            except DeviceNotRegisteredError as exc:
                return ActionHandlerResult.failed(
                    ActionResultCode.CONTROL_POLICY_MISMATCH,
                    "动作控制策略引用了未注册的停止设备: "
                    f"{target.device_id}: {exc}",
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
                "设备停止能力不满足动作控制策略: "
                f"{target.device_id} 缺少 {missing_values}",
                operation=self._CONTROL_POLICY_OPERATION,
                device_id=target.device_id,
            )
        return None
