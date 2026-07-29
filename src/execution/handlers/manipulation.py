from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...actions.circle_dispense import execute_right_arm_circle_dispense
from ...agents.powder_dispense_agent import (
    PowderDispenseAgent,
    PowderDispenseOutcome,
    config_from_params,
)
from ...device_runtime import (
    ArmId,
    ArmMotion,
    DeviceRuntime,
    DigitalOutputs,
    ExpressionDisplay,
    GripperControl,
    Pipette,
    PowderDispenser,
    ToolChanger,
)
from ...device_runtime.ids import (
    EXPRESSION_DISPLAY,
    PIPETTE,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
    TOOL_CHANGER,
)
from ..action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandler,
    ActionHandlerResult,
    ActionParameters,
    ActionResultCode,
    ActionTimeoutError,
)


BalanceReader = Callable[[], float]
TappingConfigProvider = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ManipulationHandlerOptions:
    gripper_max_attempts: int = 3
    gripper_retry_delay_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.gripper_max_attempts <= 0:
            raise ValueError("gripper_max_attempts must be positive")
        if self.gripper_retry_delay_seconds < 0:
            raise ValueError(
                "gripper_retry_delay_seconds must not be negative"
            )


@dataclass(frozen=True, slots=True)
class PipetteCommand:
    operation: str
    capacity_ul: int | None = None
    speed_ul_s: int | None = None
    full_dispense: bool = False

    @classmethod
    def from_parameters(
        cls,
        parameters: ActionParameters,
    ) -> PipetteCommand:
        operation = str(parameters.get("操作", "吸")).strip()
        if operation == "退枪头":
            return cls(operation=operation)
        if operation == "吸":
            return cls(
                operation=operation,
                capacity_ul=_positive_int(
                    parameters.get("容量", 500),
                    "吸液枪容量",
                ),
                speed_ul_s=_optional_positive_int(
                    parameters.get("吸液速度"),
                    "吸液速度",
                ),
            )
        if operation != "吐":
            raise ValueError(f"未知的吸液枪操作: {operation}")

        dispense_mode = parameters.get("吐液容量模式")
        full_dispense_value = parameters.get("全吐")
        if full_dispense_value is None:
            full_dispense = dispense_mode is None
        else:
            full_dispense = _parse_bool(full_dispense_value)
        return cls(
            operation=operation,
            capacity_ul=_positive_int(
                parameters.get("容量", 500),
                "吸液枪容量",
            ),
            speed_ul_s=_optional_positive_int(
                parameters.get("吐液速度"),
                "吐液速度",
            ),
            full_dispense=(
                full_dispense or dispense_mode == "全吐"
            ),
        )


class ManipulateActionHandler:
    """Dispatch one ARM_ACTION to its explicitly registered executor."""

    _OPERATION = "manipulate.route"

    def __init__(self, handlers: Mapping[str, ActionHandler]) -> None:
        self._handlers = dict(handlers)
        if not self._handlers:
            raise ValueError("manipulation handlers must not be empty")

    @property
    def registered_executors(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        executor = str(parameters.get("执行器", "快换手")).strip()
        handler = self._handlers.get(executor)
        if handler is None:
            return _failed_result(
                context,
                ActionResultCode.UNSUPPORTED_OPERATION,
                f"未知的执行器: {executor}",
                operation=self._OPERATION,
            )
        return handler(parameters, context)


class ToolChangerActionHandler:
    _OPERATION = "tool_changer.set_locked"

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        operation = str(parameters.get("操作", "开")).strip()
        locked_by_operation = {"开": False, "关": True}
        if operation not in locked_by_operation:
            return _failed_result(
                context,
                ActionResultCode.UNSUPPORTED_OPERATION,
                f"未知的快换手操作: {operation}",
                operation=self._OPERATION,
                device_id=TOOL_CHANGER,
            )

        try:
            tool_changer = self._device_runtime.require(
                TOOL_CHANGER,
                ToolChanger,
            )
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"快换手设备不可用: {exc}",
                operation=self._OPERATION,
                device_id=TOOL_CHANGER,
            )
        try:
            context.invoke(
                self._OPERATION,
                lambda: tool_changer.set_locked(
                    locked_by_operation[operation]
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"快换手{operation}执行失败: {exc}",
                operation=self._OPERATION,
                device_id=TOOL_CHANGER,
            )

        context.log(f"快换手{operation}执行完成", "info")
        return context.success(
            operation=self._OPERATION,
            device_id=TOOL_CHANGER,
        )


class RelayActionHandler:
    _OPERATION = "relay.set_channel"

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            channel = int(parameters.get("编号", 1))
        except (TypeError, ValueError) as exc:
            return _failed_result(
                context,
                ActionResultCode.INVALID_PARAMETERS,
                f"继电器编号无效: {exc}",
                operation=self._OPERATION,
                device_id=RELAY_BANK,
            )

        operation = str(parameters.get("操作", "开")).strip()
        if channel not in (1, 2):
            return _failed_result(
                context,
                ActionResultCode.INVALID_PARAMETERS,
                f"未知的继电器编号: {channel}",
                operation=self._OPERATION,
                device_id=RELAY_BANK,
            )
        if operation not in ("开", "关"):
            return _failed_result(
                context,
                ActionResultCode.UNSUPPORTED_OPERATION,
                f"未知的继电器操作: {operation}",
                operation=self._OPERATION,
                device_id=RELAY_BANK,
            )

        try:
            relay = self._device_runtime.require(
                RELAY_BANK,
                DigitalOutputs,
            )
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"继电器设备不可用: {exc}",
                operation=self._OPERATION,
                device_id=RELAY_BANK,
            )
        try:
            context.invoke(
                self._OPERATION,
                lambda: relay.set_channel(channel, operation == "开"),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"继电器{channel}{operation}执行失败: {exc}",
                operation=self._OPERATION,
                device_id=RELAY_BANK,
            )

        context.log(
            f"继电器{channel}{operation}执行完成",
            "info",
        )
        return context.success(
            operation=self._OPERATION,
            device_id=RELAY_BANK,
        )


class GripperActionHandler:
    _OPERATION = "gripper.execute"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        options: ManipulationHandlerOptions,
    ) -> None:
        self._device_runtime = device_runtime
        self._options = options

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        operation = str(parameters.get("操作", "开")).strip()
        if operation not in ("开", "关"):
            return _failed_result(
                context,
                ActionResultCode.UNSUPPORTED_OPERATION,
                f"未知的夹爪操作: {operation}",
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )

        try:
            gripper = self._device_runtime.require(
                ROBOT_SYSTEM,
                GripperControl,
            )
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"夹爪设备不可用: {exc}",
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )
        if operation == "开":
            action = lambda: gripper.open_gripper(ArmId.LEFT)
        else:
            action = lambda: gripper.close_gripper(ArmId.LEFT)

        context.log(f"夹爪动作: {operation}", "info")
        for attempt in range(1, self._options.gripper_max_attempts + 1):
            try:
                context.invoke(self._OPERATION, action)
            except (ActionCancelledError, ActionTimeoutError):
                raise
            except Exception as exc:
                context.log(
                    f"执行夹爪出错 "
                    f"({attempt}/{self._options.gripper_max_attempts}): "
                    f"{exc}",
                    "warn",
                )
            else:
                context.log(f"夹爪{operation}执行完成", "info")
                return context.success(
                    operation=self._OPERATION,
                    device_id=ROBOT_SYSTEM,
                )

            if attempt < self._options.gripper_max_attempts:
                context.sleep(
                    self._options.gripper_retry_delay_seconds
                )

        return _failed_result(
            context,
            ActionResultCode.DEVICE_OPERATION_FAILED,
            "夹爪重试次数耗尽",
            operation=self._OPERATION,
            device_id=ROBOT_SYSTEM,
        )


class PipetteActionHandler:
    _OPERATION = "pipette.execute"

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            command = PipetteCommand.from_parameters(parameters)
        except (TypeError, ValueError) as exc:
            return _failed_result(
                context,
                ActionResultCode.INVALID_PARAMETERS,
                f"吸液枪参数无效: {exc}",
                operation=self._OPERATION,
                device_id=PIPETTE,
            )

        try:
            pipette = self._device_runtime.require(PIPETTE, Pipette)
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"吸液枪设备不可用: {exc}",
                operation=self._OPERATION,
                device_id=PIPETTE,
            )
        try:
            if command.operation == "退枪头":
                success = context.invoke(
                    "pipette.eject_tip",
                    pipette.eject_tip,
                )
            elif command.operation == "吸":
                success = self._absorb(
                    pipette,
                    command,
                    context,
                )
            else:
                success = self._dispense(
                    pipette,
                    command,
                    context,
                )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"执行吸液枪出错: {exc}",
                operation=self._OPERATION,
                device_id=PIPETTE,
            )

        level = "info" if success else "error"
        context.log(
            f"吸液枪{command.operation}执行"
            f"{'成功' if success else '失败'}",
            level,
        )
        if success:
            return context.success(
                operation=self._OPERATION,
                device_id=PIPETTE,
            )
        return context.failure(
            ActionResultCode.OPERATION_REJECTED,
            f"吸液枪{command.operation}执行失败",
            operation=self._OPERATION,
            device_id=PIPETTE,
            log=False,
        )

    @staticmethod
    def _absorb(
        pipette: Pipette,
        command: PipetteCommand,
        context: ActionExecutionContext,
    ) -> bool:
        capacity_ul = _required_capacity(command)
        if command.speed_ul_s is not None:
            context.log(
                f"正在设置吸液速度: {command.speed_ul_s}ul/s",
                "info",
            )
            configured = context.invoke(
                "pipette.set_absorb_speed",
                lambda: pipette.set_absorb_speed(
                    command.speed_ul_s
                ),
            )
            if not configured:
                context.log("设置吸液速度失败", "error")
                return False

        context.log("正在吸液...", "info")
        return context.invoke(
            "pipette.absorb",
            lambda: pipette.absorb(capacity_ul),
        )

    @staticmethod
    def _dispense(
        pipette: Pipette,
        command: PipetteCommand,
        context: ActionExecutionContext,
    ) -> bool:
        capacity_ul = _required_capacity(command)
        if command.speed_ul_s is not None:
            context.log(
                f"正在设置吐液速度: {command.speed_ul_s}ul/s",
                "info",
            )
            configured = context.invoke(
                "pipette.set_dispense_speed",
                lambda: pipette.set_dispense_speed(
                    command.speed_ul_s
                ),
            )
            if not configured:
                context.log("设置吐液速度失败", "error")
                return False

        context.log("正在吐液...", "info")
        if command.full_dispense:
            return context.invoke(
                "pipette.dispense_all",
                pipette.dispense_all,
            )
        return context.invoke(
            "pipette.dispense",
            lambda: pipette.dispense(capacity_ul),
        )


class ExpressionDisplayActionHandler:
    _CLOSE_OPERATIONS = frozenset({"关闭", "close"})
    _SHUTDOWN_OPERATION = "expression_display.shutdown"
    _SWITCH_OPERATION = "expression_display.switch"

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        operation = str(parameters.get("操作", "切换")).strip().lower()
        if operation in self._CLOSE_OPERATIONS:
            try:
                context.invoke(
                    self._SHUTDOWN_OPERATION,
                    lambda: self._device_runtime.shutdown(
                        EXPRESSION_DISPLAY
                    ),
                )
            except (ActionCancelledError, ActionTimeoutError):
                raise
            except Exception as exc:
                return _failed_result(
                    context,
                    ActionResultCode.DEVICE_OPERATION_FAILED,
                    f"表情屏关闭失败: {exc}",
                    operation=self._SHUTDOWN_OPERATION,
                    device_id=EXPRESSION_DISPLAY,
                )
            context.log("表情屏连接已关闭", "info")
            return context.success(
                operation=self._SHUTDOWN_OPERATION,
                device_id=EXPRESSION_DISPLAY,
            )

        expression = _first_non_empty(
            parameters,
            ("表情", "表情名称", "expression", "name"),
        )
        if expression is None:
            return _failed_result(
                context,
                ActionResultCode.INVALID_PARAMETERS,
                "表情屏动作缺少表情名称",
                operation=self._SWITCH_OPERATION,
                device_id=EXPRESSION_DISPLAY,
            )

        try:
            display = self._device_runtime.require(
                EXPRESSION_DISPLAY,
                ExpressionDisplay,
            )
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"表情屏设备不可用: {exc}",
                operation=self._SWITCH_OPERATION,
                device_id=EXPRESSION_DISPLAY,
            )
        context.log(f"表情屏切换: {expression}", "info")
        try:
            switched = context.invoke(
                self._SWITCH_OPERATION,
                lambda: display.switch(expression),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"表情屏切换失败: {exc}",
                operation=self._SWITCH_OPERATION,
                device_id=EXPRESSION_DISPLAY,
            )

        name = getattr(switched, "name", str(switched))
        context.log(f"表情屏切换完成: {name}", "info")
        return context.success(
            operation=self._SWITCH_OPERATION,
            device_id=EXPRESSION_DISPLAY,
        )


class TappingActionHandler:
    _OPERATION = "powder_dispenser.execute"
    _ENABLE_OPERATION = "powder_dispenser.enable_all"
    _SIMPLE_OPERATIONS = frozenset(
        {
            "夹爪闭合",
            "夹爪张开",
            "针停止",
            "针旋转停止",
            "使能",
        }
    )
    _STEP_OPERATIONS = frozenset(
        {"针上升", "针下降", "针正转", "针反转"}
    )
    _SUPPORTED_OPERATIONS = (
        _SIMPLE_OPERATIONS
        | _STEP_OPERATIONS
        | {"夹爪移动到"}
    )

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        operation = str(parameters.get("操作", "")).strip()
        if operation not in self._SUPPORTED_OPERATIONS:
            return _failed_result(
                context,
                ActionResultCode.UNSUPPORTED_OPERATION,
                f"未知的加粉装置操作: {operation}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )
        try:
            argument = self._parse_argument(operation, parameters)
        except (TypeError, ValueError) as exc:
            return _failed_result(
                context,
                ActionResultCode.INVALID_PARAMETERS,
                f"加粉装置参数无效: {exc}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )

        try:
            controller = self._device_runtime.require(
                POWDER_DISPENSER,
                PowderDispenser,
            )
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"加粉装置不可用: {exc}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )
        action = self._resolve_action(
            controller,
            operation,
            argument,
        )

        context.log(f"加粉装置动作: {operation}", "info")
        try:
            context.invoke(
                self._ENABLE_OPERATION,
                controller.enable_all,
            )
            if operation != "使能":
                context.invoke(
                    f"powder_dispenser.{operation}",
                    action,
                )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"加粉装置 {operation} 执行失败: {exc}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )

        context.log(f"加粉装置 {operation} 执行完成", "info")
        return context.success(
            operation=self._OPERATION,
            device_id=POWDER_DISPENSER,
        )

    @staticmethod
    def _resolve_action(
        controller: PowderDispenser,
        operation: str,
        argument: int | None,
    ) -> Callable[[], None]:
        simple_operations: dict[str, Callable[[], None]] = {
            "夹爪闭合": controller.gripper_grip,
            "夹爪张开": controller.gripper_release,
            "针停止": controller.lift_stop,
            "针旋转停止": controller.rotation_stop,
            "使能": controller.enable_all,
        }
        if operation in simple_operations:
            return simple_operations[operation]
        if argument is None:
            raise RuntimeError(
                f"missing validated argument for operation: {operation}"
            )
        if operation == "夹爪移动到":
            return lambda: controller.gripper_move_to(argument)

        step_operations = {
            "针上升": controller.lift_up,
            "针下降": controller.lift_down,
            "针正转": controller.rotation_cw,
            "针反转": controller.rotation_ccw,
        }
        step_operation = step_operations[operation]
        return lambda: step_operation(argument)

    @classmethod
    def _parse_argument(
        cls,
        operation: str,
        parameters: ActionParameters,
    ) -> int | None:
        if operation == "夹爪移动到":
            opening_percent = int(parameters.get("开度", 50))
            if not 0 <= opening_percent <= 100:
                raise ValueError("夹爪开度必须在0到100之间")
            return opening_percent
        if operation in cls._STEP_OPERATIONS:
            steps = int(parameters.get("步数", 5000))
            if steps <= 0:
                raise ValueError("步数必须大于0")
            return steps
        return None


class CircleDispenseActionHandler:
    _OPERATION = "circle_dispense.execute"

    def __init__(self, device_runtime: DeviceRuntime) -> None:
        self._device_runtime = device_runtime

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            motion = self._device_runtime.require(
                ROBOT_SYSTEM,
                ArmMotion,
            )
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"转圈注液机械臂不可用: {exc}",
                operation=self._OPERATION,
                device_id=ROBOT_SYSTEM,
            )
        try:
            pipette = self._device_runtime.require(PIPETTE, Pipette)
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"转圈注液吸液枪不可用: {exc}",
                operation=self._OPERATION,
                device_id=PIPETTE,
            )

        try:
            success = context.invoke(
                self._OPERATION,
                lambda: execute_right_arm_circle_dispense(
                    robot_motion=motion,
                    pipette=pipette,
                    params=dict(parameters),
                    log=context.log,
                    stop_requested=lambda: context.stop_requested,
                    paused=lambda: context.paused,
                ),
            )
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"转圈注液执行失败: {exc}",
                operation=self._OPERATION,
            )

        if success:
            return context.success(
                operation=self._OPERATION,
            )
        return _failed_result(
            context,
            ActionResultCode.OPERATION_REJECTED,
            "转圈注液执行失败",
            operation=self._OPERATION,
        )


class PowderDispenseActionHandler:
    _OPERATION = "powder_dispense.run"

    def __init__(
        self,
        device_runtime: DeviceRuntime,
        tapping_config_provider: TappingConfigProvider,
        *,
        read_balance: BalanceReader | None = None,
    ) -> None:
        self._device_runtime = device_runtime
        self._tapping_config_provider = tapping_config_provider
        self._read_balance = read_balance

    def __call__(
        self,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        try:
            config = config_from_params(
                dict(parameters),
                dict(self._tapping_config_provider()),
            )
        except (TypeError, ValueError) as exc:
            return _failed_result(
                context,
                ActionResultCode.INVALID_PARAMETERS,
                f"智能加粉参数无效: {exc}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )

        context.log(
            f"智能加粉动作: 目标={config.target_mg:.1f}mg, "
            f"容差={config.tolerance_mg:.1f}mg, "
            f"最大轮次={config.max_rounds}",
            "info",
        )
        try:
            controller = self._device_runtime.require(
                POWDER_DISPENSER,
                PowderDispenser,
            )
            balance_reader = self._resolve_balance_reader()
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_UNAVAILABLE,
                f"智能加粉依赖不可用: {exc}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )
        agent = PowderDispenseAgent(
            controller,
            balance_reader,
            log=lambda message: context.log(message, "info"),
            should_stop=lambda: context.stop_requested,
            sleep=context.sleep,
        )
        try:
            result = agent.run(config)
            context.checkpoint()
        except (ActionCancelledError, ActionTimeoutError):
            raise
        except Exception as exc:
            return _failed_result(
                context,
                ActionResultCode.DEVICE_OPERATION_FAILED,
                f"智能加粉执行失败: {exc}",
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )

        level = "info" if result.successful else "error"
        message = (
            f"智能加粉结束: {result.message}, "
            f"已加={result.added_mg:.1f}mg/{result.target_mg:.1f}mg, "
            f"轮次={result.rounds}, 终值={result.final_g:.4f}g"
        )
        context.log(message, level)
        if result.successful:
            return context.success(
                message=result.message,
                operation=self._OPERATION,
                device_id=POWDER_DISPENSER,
            )
        failure_code = (
            ActionResultCode.TARGET_NOT_REACHED
            if result.outcome is PowderDispenseOutcome.MAX_ROUNDS_REACHED
            else ActionResultCode.OPERATION_REJECTED
        )
        return context.failure(
            failure_code,
            message,
            operation=self._OPERATION,
            device_id=POWDER_DISPENSER,
            log=False,
        )

    def _resolve_balance_reader(self) -> BalanceReader:
        if self._read_balance is not None:
            return self._read_balance
        from ...vision.balance_reader_simple import read_balance

        return read_balance


def create_manipulation_handler(
    device_runtime: DeviceRuntime,
    options: ManipulationHandlerOptions,
    tapping_config_provider: TappingConfigProvider,
) -> ManipulateActionHandler:
    expression_handler = ExpressionDisplayActionHandler(device_runtime)
    handlers: dict[str, ActionHandler] = {
        "快换手": ToolChangerActionHandler(device_runtime),
        "继电器": RelayActionHandler(device_runtime),
        "夹爪": GripperActionHandler(device_runtime, options),
        "吸液枪": PipetteActionHandler(device_runtime),
        "表情屏": expression_handler,
        "表情": expression_handler,
        "expression_display": expression_handler,
        "expression": expression_handler,
        "右臂转圈注液": CircleDispenseActionHandler(device_runtime),
        "智能加粉": PowderDispenseActionHandler(
            device_runtime,
            tapping_config_provider,
        ),
        "加粉装置": TappingActionHandler(device_runtime),
    }
    return ManipulateActionHandler(handlers)


def _positive_int(value: object, label: str) -> int:
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{label}必须大于0")
    return normalized


def _required_capacity(command: PipetteCommand) -> int:
    if command.capacity_ul is None:
        raise RuntimeError(
            f"missing capacity for pipette operation: {command.operation}"
        )
    return command.capacity_ul


def _optional_positive_int(
    value: object,
    label: str,
) -> int | None:
    if value is None or value == "":
        return None
    return _positive_int(value, label)


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "是"}:
            return True
        if normalized in {"0", "false", "no", "off", "否"}:
            return False
        raise ValueError(f"无法识别布尔值: {value}")
    if isinstance(value, (int, float)):
        return bool(value)
    raise TypeError(f"不支持的布尔值类型: {type(value).__name__}")


def _first_non_empty(
    parameters: ActionParameters,
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = parameters.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _failed_result(
    context: ActionExecutionContext,
    code: ActionResultCode,
    message: str,
    *,
    operation: str,
    device_id: str = "",
) -> ActionHandlerResult:
    return context.failure(
        code,
        message,
        operation=operation,
        device_id=device_id,
    )
