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
from ..device_runtime import DeviceRuntime
from .action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerRegistry,
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

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        config: Any,
    ) -> None:
        self._device_runtime = device_runtime
        self.execution_context = ExecutionContext()
        self._callbacks: EngineCallbacks | None = None
        self._last_error = ""
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
        registry.register(
            ActionType.MOVE,
            MoveActionHandler(
                RobotMoveActionHandler(
                    self._device_runtime,
                    self.execution_context,
                    self._motion_handler_options,
                ),
                BodyMoveActionHandler(
                    self._device_runtime,
                    self._motion_handler_options,
                ),
            ),
        )
        registry.register(
            ActionType.BASE_MOVE,
            BaseMoveActionHandler(self._device_runtime),
        )
        registry.register(
            ActionType.MANIPULATE,
            create_manipulation_handler(
                self._device_runtime,
                self._manipulation_handler_options,
                self._tapping_config_provider,
            ),
        )
        registry.register(ActionType.INSPECT, InspectActionHandler())
        registry.register(ActionType.WAIT, WaitActionHandler())
        registry.register(
            ActionType.CHANGE_GUN,
            ChangeToolActionHandler(self._device_runtime),
        )
        registry.register(
            ActionType.VISION_CAPTURE,
            VisionCaptureActionHandler(self._device_runtime),
        )
        registry.register(
            ActionType.VISION_RELOCALIZE,
            VisionRelocalizationActionHandler(
                self._device_runtime,
                self.execution_context,
            ),
        )
        registry.register(
            ActionType.TRAJECTORY,
            TrajectoryActionHandler(
                self._device_runtime,
                self._trajectory_handler_options,
            ),
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
        failure = ""
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

                item.status = SequenceItemStatus.RUNNING
                self._last_error = ""
                self._on_step_started(index, item)

                try:
                    success = self._execute_action(item, control)
                    if success:
                        item.status = SequenceItemStatus.SUCCESS
                        self._on_step_completed(index, item)
                    elif control.cancel_requested:
                        item.status = SequenceItemStatus.PENDING
                        return EngineResult(success=False, cancelled=True)
                    else:
                        item.status = SequenceItemStatus.FAILED
                        failure = (
                            self._last_error
                            or f"动作执行失败: {item.definition.name}"
                        )
                        self._on_step_failed(index, item, failure)
                        break
                except Exception as e:
                    item.status = SequenceItemStatus.FAILED
                    failure = f"执行异常: {str(e)}"
                    self._on_step_failed(index, item, failure)
                    break
        finally:
            self._callbacks = None

        if control.cancel_requested:
            return EngineResult(success=False, cancelled=True)
        if failure:
            return EngineResult(success=False, error=failure)
        return EngineResult(success=True)

    def _required_callbacks(self) -> EngineCallbacks:
        if self._callbacks is None:
            raise RuntimeError("action engine callbacks are unavailable")
        return self._callbacks

    def _on_step_started(self, index: int, item: SequenceItem) -> None:
        self._required_callbacks().on_step_started(index, item)

    def _on_step_completed(self, index: int, item: SequenceItem) -> None:
        self._required_callbacks().on_step_completed(index, item)

    def _on_step_failed(
        self,
        index: int,
        item: SequenceItem,
        error: str,
    ) -> None:
        self._required_callbacks().on_step_failed(index, item, error)

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
        if level == "error":
            self._last_error = message
        self._required_callbacks().on_log(message, level)

    # ------------------------------------------------------------------
    # 动作分发（与 execution.py 逻辑一致）
    # ------------------------------------------------------------------

    def _execute_action(
        self,
        item: SequenceItem,
        control: ExecutionControl,
    ) -> bool:
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
            self._on_log(f"动作超时配置无效: {exc}", "error")
            return False

        try:
            return self._handler_registry.execute(
                definition.type,
                params,
                context,
            )
        except ActionCancelledError:
            self._on_log(f"动作已取消: {definition.name}")
            return False
        except ActionTimeoutError as exc:
            self._on_log(str(exc), "error")
            return False
        except Exception as exc:
            self._on_log(f"执行错误: {str(exc)}", "error")
            return False
