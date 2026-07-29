"""Synchronous action engine used exclusively by ExecutionManager."""

from collections.abc import Sequence
import logging
from pathlib import Path
from typing import Any

from ..actions.circle_dispense import execute_right_arm_circle_dispense
from ..core.execution_context import ExecutionContext
from ..core.models import (
    ActionType,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ..core.move_compensation import resolve_robot_target_pose
from ..device_runtime import (
    ArmId,
    ArmMotion,
    BodyAxis,
    CameraSource,
    CartesianPose,
    DepthCameraSource,
    DeviceRuntime,
    DigitalOutputs,
    ExpressionDisplay,
    GripperControl,
    MobileBase,
    MotionMode,
    Pipette,
    PowderDispenser,
    RobotSystem,
    ToolRackControl,
    ToolChanger,
    TrajectoryControl,
)
from ..device_runtime.ids import (
    BODY_AXIS,
    CAMERA,
    EXPRESSION_DISPLAY,
    MOBILE_BASE,
    PIPETTE,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
    TOOL_CHANGER,
)
from .action_handlers import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerRegistry,
    ActionParameters,
    ActionTimeoutError,
    InspectActionHandler,
    WaitActionHandler,
)
from .control import ExecutionControl
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
        self.config = config
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
        self._handler_registry = self._create_handler_registry()

    def _create_handler_registry(self) -> ActionHandlerRegistry:
        registry = ActionHandlerRegistry()
        registry.register(ActionType.MOVE, self._execute_move)
        registry.register(ActionType.BASE_MOVE, self._execute_base_move)
        registry.register(ActionType.MANIPULATE, self._execute_manipulate)
        registry.register(ActionType.INSPECT, InspectActionHandler())
        registry.register(ActionType.WAIT, WaitActionHandler())
        registry.register(ActionType.CHANGE_GUN, self._execute_change_gun)
        registry.register(
            ActionType.VISION_CAPTURE,
            self._execute_vision_capture,
        )
        registry.register(
            ActionType.VISION_RELOCALIZE,
            self._execute_vision_relocalize,
        )
        registry.register(ActionType.TRAJECTORY, self._execute_trajectory)
        registry.validate_complete()
        return registry

    @property
    def _body_controller(self) -> BodyAxis | None:
        return self._device_runtime.get_if_ready(BODY_AXIS)

    @property
    def _move_controller(self) -> MobileBase | None:
        return self._device_runtime.get_if_ready(MOBILE_BASE)

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

    # ------------------------------------------------------------------
    # 移动类动作
    # ------------------------------------------------------------------

    def _execute_move(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        target = params.get('目标', '机械臂')
        if target == '身体':
            return self._execute_body_move(params, context)
        else:
            return self._execute_robot_move(params, context)

    def _execute_robot_move(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行机械臂移动"""
        arm = params.get('臂', '左')
        target_pose_str = params.get('点位', '')
        mode = params.get('模式', '')

        self._on_log(f"机械臂移动动作: 臂={arm}, 模式={mode}, 点位={target_pose_str}")

        try:
            target_pose = resolve_robot_target_pose(
                params,
                arm,
                self.execution_context,
                self._on_log,
            )

            arm_id = ArmId.parse(arm)
            motion_mode = MotionMode.parse(mode)
            pose = CartesianPose.from_iterable(target_pose)
            motion = self._device_runtime.require(ROBOT_SYSTEM, ArmMotion)

            # 重试机制：处理通信抖动
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    motion.move_to_pose(
                        arm_id,
                        pose,
                        motion_mode,
                    )
                    self._on_log("机械臂移动执行完成")
                    return True
                except Exception as exc:
                    self._on_log(
                        f"机械臂移动失败 (第{attempt}次): {exc}",
                        "warn",
                    )
                context.sleep(0.5)

            self._on_log("机械臂移动重试次数耗尽", "error")
            return False
        except Exception as e:
            self._on_log(f"执行机械臂移动出错: {str(e)}", "error")
            return False

    def _execute_body_move(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行身体移动（ModbusMotor）"""
        position = params.get('位置', 0)

        self._on_log(f"身体移动动作: 目标位置={position}")

        if self._body_controller is None:
            self._on_log("身体控制器未初始化", "error")
            return False

        try:
            self._on_log(f"正在移动身体到位置 {position}...")
            self._body_controller.move_to(position)

            # 等待到达目标位置
            while True:
                if context.stop_requested:
                    self._on_log("身体移动已停止")
                    return False
                st = self._body_controller.is_reached()
                if st is None:
                    self._on_log("身体通信异常", "error")
                    return False
                if st:
                    self._on_log(f"身体移动完成，位置={position}")
                    return True
                context.sleep(0.1)
        except Exception as e:
            self._on_log(f"执行身体移动出错: {str(e)}", "error")
            return False

    def _execute_base_move(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行底盘移动（统一入口，根据 move_mode 区分）"""
        move_mode = params.get('move_mode', 'position')
        
        if move_mode == 'position':
            return self._execute_base_move_position(params, context)
        elif move_mode == 'distance':
            return self._execute_base_move_distance(params, context)
        else:
            self._on_log(f"未知的移动方式：{move_mode}", "error")
            return False

    def _execute_base_move_position(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行底盘位置移动"""
        id_value = params.get('id', 0)
        cid = params.get('cid', 0)
        
        self._on_log(f"底盘位置移动：ID={id_value}, CID={cid}")
        
        if self._move_controller is None:
            self._on_log("底盘移动控制器未初始化", "error")
            return False
        
        try:
            success = context.invoke(
                "mobile_base.move_to_position",
                lambda: self._move_controller.move_to_position(
                    id_value,
                    cid,
                ),
            )
            
            if success:
                self._on_log(f"底盘位置移动完成：ID={id_value}, CID={cid}")
            else:
                self._on_log(f"底盘位置移动失败：ID={id_value}, CID={cid}", "error")
            
            return success
        except Exception as e:
            self._on_log(f"执行底盘位置移动出错：{str(e)}", "error")
            return False

    def _execute_base_move_distance(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行底盘距离移动"""
        x = params.get('x', 0.0)
        y = params.get('y', 0.0)
        angle = params.get('angle', 0.0)

        self._on_log(f"底盘距离移动：x={x}cm, y={y}cm, angle={angle}°")

        if self._move_controller is None:
            self._on_log("底盘移动控制器未初始化", "error")
            return False

        try:
            success = context.invoke(
                "mobile_base.move_slowly",
                lambda: self._move_controller.move_slowly(x, y, angle),
            )

            if success:
                self._on_log(f"底盘距离移动完成：x={x}cm, y={y}cm, angle={angle}°")
            else:
                self._on_log(f"底盘距离移动失败：x={x}cm, y={y}cm, angle={angle}°", "error")

            return success
        except Exception as e:
            self._on_log(f"执行底盘距离移动出错：{str(e)}", "error")
            return False

    # ------------------------------------------------------------------
    # 操作类动作
    # ------------------------------------------------------------------

    def _execute_manipulate(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        executor = params.get('执行器', '快换手')
        number = params.get('编号', 1)
        operation = params.get('操作', '开')

        if executor == '快换手':
            tool_changer = self._device_runtime.require(
                TOOL_CHANGER,
                ToolChanger,
            )
            if operation == '开':
                tool_changer.set_locked(False)
            elif operation == '关':
                tool_changer.set_locked(True)
            else:
                self._on_log(f"未知的快换手操作: {operation}", "error")
                return False

        elif executor == '继电器':
            if number not in (1, 2):
                self._on_log(f"未知的编号: {number}", "error")
                return False
            if operation not in ('开', '关'):
                self._on_log(f"未知的继电器操作: {operation}", "error")
                return False
            relay = self._device_runtime.require(RELAY_BANK, DigitalOutputs)
            relay.set_channel(number, operation == '开')

        elif executor == '夹爪':
            return self._execute_gripper(operation, context)
        elif executor == '吸液枪':
            return self._execute_pipette(params, context)
        elif executor in ('表情屏', '表情', 'expression_display', 'expression'):
            return self._execute_expression_display(params, context)
        elif executor == '右臂转圈注液':
            pipette = self._device_runtime.require(PIPETTE, Pipette)
            return execute_right_arm_circle_dispense(
                robot_motion=self._device_runtime.require(
                    ROBOT_SYSTEM,
                    ArmMotion,
                ),
                pipette=pipette,
                params=params,
                log=self._on_log,
                stop_requested=lambda: context.stop_requested,
                paused=lambda: context.paused,
            )
        elif executor == '智能加粉':
            return self._execute_powder_dispense(params, context)
        elif executor == '加粉装置':
            return self._execute_tapping(params, context)
        else:
            self._on_log(f"未知的执行器: {executor}", "error")
            return False

        self._on_log(f"执行器: {executor}, 编号: {number}, 操作: {operation}")
        return True

    def _execute_expression_display(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        operation = str(params.get('操作', '切换')).lower()
        if operation in ('关闭', 'close'):
            self._device_runtime.shutdown(EXPRESSION_DISPLAY)
            self._on_log("表情屏连接已关闭")
            return True

        expression = (
            params.get('表情')
            or params.get('表情名称')
            or params.get('expression')
            or params.get('name')
        )
        if expression is None or str(expression).strip() == "":
            self._on_log("表情屏动作缺少表情名称", "error")
            return False

        self._on_log(f"表情屏切换: {expression}")
        try:
            display = self._device_runtime.require(
                EXPRESSION_DISPLAY,
                ExpressionDisplay,
            )
            switched = display.switch(str(expression))
            name = getattr(switched, "name", str(switched))
            self._on_log(f"表情屏切换完成: {name}")
            return True
        except Exception as e:
            self._on_log(f"表情屏切换失败: {str(e)}", "error")
            return False

    def _execute_gripper(
        self,
        operation: str,
        context: ActionExecutionContext,
    ) -> bool:
        """执行夹爪动作"""
        self._on_log(f"夹爪动作: {operation}")

        gripper = self._device_runtime.require(
            ROBOT_SYSTEM,
            GripperControl,
        )

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if operation == '开':
                    gripper.open_gripper(ArmId.LEFT)
                elif operation == '关':
                    gripper.close_gripper(ArmId.LEFT)
                else:
                    self._on_log(f"未知的夹爪操作: {operation}", "error")
                    return False
                self._on_log(f"夹爪{operation}执行完成")
                return True
            except Exception as e:
                self._on_log(f"执行夹爪出错: {str(e)} (第{attempt}次)", "warn")
            context.sleep(0.5)

        self._on_log("夹爪重试次数耗尽", "error")
        return False

    def _execute_tapping(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行加粉装置动作（夹爪/针升降/针旋转）。"""
        operation = params.get('操作', '')
        self._on_log(f"加粉装置动作: {operation}")

        operations = {
            "夹爪闭合": lambda ctrl: ctrl.gripper_grip(),
            "夹爪张开": lambda ctrl: ctrl.gripper_release(),
            "夹爪移动到": lambda ctrl: ctrl.gripper_move_to(
                int(params.get("开度", 50))
            ),
            "针上升": lambda ctrl: ctrl.lift_up(
                int(params.get("步数", 5000))
            ),
            "针下降": lambda ctrl: ctrl.lift_down(
                int(params.get("步数", 5000))
            ),
            "针停止": lambda ctrl: ctrl.lift_stop(),
            "针正转": lambda ctrl: ctrl.rotation_cw(
                int(params.get("步数", 5000))
            ),
            "针反转": lambda ctrl: ctrl.rotation_ccw(
                int(params.get("步数", 5000))
            ),
            "针旋转停止": lambda ctrl: ctrl.rotation_stop(),
            "使能": lambda ctrl: ctrl.enable_all(),
        }
        action = operations.get(operation)
        if action is None:
            self._on_log(f"未知的加粉装置操作: {operation}", "error")
            return False

        ctrl = self._device_runtime.require(
            POWDER_DISPENSER,
            PowderDispenser,
        )
        try:
            ctrl.enable_all()
            action(ctrl)
            self._on_log(f"加粉装置 {operation} 执行完成")
            return True
        except Exception as e:
            self._on_log(f"加粉装置 {operation} 执行失败: {e}", "error")
            return False

    def _execute_powder_dispense(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行智能闭环加粉动作。"""
        from ..agents.powder_dispense_agent import PowderDispenseAgent, config_from_params
        from ..vision.balance_reader_simple import read_balance

        config = config_from_params(
            params,
            self.config.get_tapping_config(),
        )
        self._on_log(
            f"智能加粉动作: 目标={config.target_mg:.1f}mg, "
            f"容差={config.tolerance_mg:.1f}mg, 最大轮次={config.max_rounds}"
        )

        controller = self._device_runtime.require(
            POWDER_DISPENSER,
            PowderDispenser,
        )
        agent = PowderDispenseAgent(
            controller,
            read_balance,
            log=lambda msg: self._on_log(msg),
            should_stop=lambda: context.stop_requested,
        )
        try:
            result = agent.run(config)
        except Exception as e:
            self._on_log(f"智能加粉执行失败: {e}", "error")
            return False

        level = "info" if result.success else "error"
        self._on_log(
            f"智能加粉结束: {result.message}, "
            f"已加={result.added_mg:.1f}mg/{result.target_mg:.1f}mg, "
            f"轮次={result.rounds}, 终值={result.final_g:.4f}g",
            level,
        )
        return result.success

    def _execute_pipette(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行吸液枪动作（吸/吐）"""
        operation = params.get('操作', '吸')
        capacity = params.get('容量', 500)
        absorb_speed = params.get('吸液速度')
        dispense_speed = params.get('吐液速度')
        dispense_mode = params.get('吐液容量模式')
        full_dispense = params.get('全吐')
        if full_dispense is None:
            full_dispense = operation == '吐' and dispense_mode is None
        full_dispense = bool(full_dispense or dispense_mode == '全吐')

        self._on_log(
            f"吸液枪动作: 操作={operation}, 容量={capacity}ul, "
            f"吸液速度={absorb_speed or '-'}ul/s, 吐液速度={dispense_speed or '-'}ul/s"
        )

        try:
            pipette = self._device_runtime.require(PIPETTE, Pipette)
            if operation == '吸':
                if absorb_speed:
                    self._on_log(f"正在设置吸液速度: {absorb_speed}ul/s")
                    if not pipette.set_absorb_speed(int(absorb_speed)):
                        self._on_log("设置吸液速度失败", "error")
                        ret = False
                    else:
                        self._on_log("正在吸液...")
                        ret = pipette.absorb(int(capacity))
                else:
                    self._on_log("正在吸液...")
                    ret = pipette.absorb(int(capacity))
            elif operation == '吐':
                if dispense_speed:
                    self._on_log(f"正在设置吐液速度: {dispense_speed}ul/s")
                    if not pipette.set_dispense_speed(int(dispense_speed)):
                        self._on_log("设置吐液速度失败", "error")
                        ret = False
                    else:
                        self._on_log("正在吐液...")
                        ret = (
                            pipette.dispense_all()
                            if full_dispense
                            else pipette.dispense(int(capacity))
                        )
                else:
                    self._on_log("正在吐液...")
                    ret = (
                        pipette.dispense_all()
                        if full_dispense
                        else pipette.dispense(int(capacity))
                    )
            elif operation == '退枪头':
                self._on_log("正在退枪头...")
                ret = pipette.eject_tip()
            else:
                self._on_log(f"未知的吸液枪操作: {operation}", "error")
                return False

            if ret:
                self._on_log(f"吸液枪{operation}执行成功")
            else:
                self._on_log(f"吸液枪{operation}执行失败", "error")
            return ret
        except Exception as e:
            self._on_log(f"执行吸液枪出错: {str(e)}", "error")
            return False

    def _execute_trajectory(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        robot_name = params.get("robot", "robot1")
        file_path = params.get("file_path", "")

        self._on_log(f"执行轨迹动作: robot={robot_name}, file={file_path}")

        if not file_path or not Path(file_path).exists():
            self._on_log(f"轨迹文件不存在: {file_path}", "error")
            return False

        try:
            arm = ArmId.parse(robot_name)
            trajectory = self._device_runtime.require(
                ROBOT_SYSTEM,
                TrajectoryControl,
            )
            context.invoke(
                "trajectory.send",
                lambda: trajectory.send_trajectory(arm, file_path),
            )

            while True:
                context.checkpoint()
                if trajectory.is_trajectory_complete(arm):
                    self._on_log("轨迹执行完成")
                    return True
                context.sleep(0.5)
        except Exception as e:
            self._on_log(f"轨迹执行异常: {e}", "error")
            return False

    # ------------------------------------------------------------------
    # 换枪类动作
    # ------------------------------------------------------------------

    def _execute_change_gun(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行换枪动作"""
        gun_position = params.get('Gun_Position', 1)
        operation = params.get('Operation', '取')

        self._on_log(f"换枪动作: 枪位={gun_position}, 操作={operation}")

        try:
            if gun_position not in (1, 2) or operation not in ('取', '放'):
                self._on_log(f"未知的换枪参数组合: 枪位={gun_position}, 操作={operation}", "error")
                return False
            tool_rack = self._device_runtime.require(
                ROBOT_SYSTEM,
                ToolRackControl,
            )
            tool_rack.change_tool(
                int(gun_position),
                attach=operation == '取',
            )
            self._on_log(
                f"工具架操作完成: slot={gun_position}, operation={operation}"
            )
            return True
        except Exception as e:
            self._on_log(f"执行换枪出错: {str(e)}", "error")
            return False

    # ------------------------------------------------------------------
    # 视觉抓取动作
    # ------------------------------------------------------------------

    def _execute_vision_capture(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行视觉抓取动作（委托共用模块）"""
        from ..vision.executor import execute_vision_capture
        camera = self._device_runtime.require(CAMERA, DepthCameraSource)
        return execute_vision_capture(
            self._device_runtime.require(ROBOT_SYSTEM, RobotSystem),
            camera,
            params,
            self._on_log,
        )

    def _execute_vision_relocalize(
        self,
        params: ActionParameters,
        context: ActionExecutionContext,
    ) -> bool:
        """执行视觉重定位动作。"""
        from ..vision.relocalization import execute_vision_relocalization

        try:
            return execute_vision_relocalization(
                self._device_runtime.require(ROBOT_SYSTEM, RobotSystem),
                self._device_runtime.require(CAMERA, CameraSource),
                params,
                self.execution_context,
                self._on_log,
            )
        except Exception as exc:
            self._on_log(f"视觉重定位失败: {exc}", "error")
            return False
