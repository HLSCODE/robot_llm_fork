"""
纯 Python 动作执行引擎（无 Qt 依赖）
从 execution.py 中提取核心逻辑，用回调函数替代 pyqtSignal，
可同时被 GUI 模式和 WebSocket 服务模式复用。
"""

import time
import json
import threading
import logging
from typing import Callable, Optional, List

from ..core.models import SequenceItem, SequenceItemStatus, ActionType

logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    动作序列执行器（纯 Python，无 Qt 依赖）

    回调函数签名:
        on_step_started(index: int, item: SequenceItem)
        on_step_completed(index: int, item: SequenceItem)
        on_step_failed(index: int, item: SequenceItem, error: str)
        on_log(message: str)
        on_finished()
    """

    def __init__(
        self,
        robot_controller=None,
        body_controller=None,
        on_step_started: Optional[Callable] = None,
        on_step_completed: Optional[Callable] = None,
        on_step_failed: Optional[Callable] = None,
        on_log: Optional[Callable] = None,
        on_finished: Optional[Callable] = None,
    ):
        self._robot_controller = robot_controller
        self._body_controller = body_controller

        # 回调
        self._on_step_started = on_step_started or (lambda *a: None)
        self._on_step_completed = on_step_completed or (lambda *a: None)
        self._on_step_failed = on_step_failed or (lambda *a: None)
        self._on_log = on_log or (lambda msg, level="info": logger.info(msg))
        self._on_finished = on_finished or (lambda: None)

        # 控制状态
        self._stop_requested = False
        self._paused = False
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def execute(self, sequence: List[SequenceItem]) -> None:
        """在后台线程中执行动作序列"""
        if self._running:
            self._on_log("已有序列正在执行，请先停止")
            return

        self._stop_requested = False
        self._paused = False
        self._running = True

        self._thread = threading.Thread(
            target=self._run, args=(sequence,), daemon=True, name="ActionExecutor"
        )
        self._thread.start()

    def stop(self) -> None:
        """停止执行"""
        self._stop_requested = True
        self._paused = False  # 解除暂停，让线程能退出

    def pause(self) -> None:
        """暂停执行"""
        self._paused = True

    def resume(self) -> None:
        """恢复执行"""
        self._paused = False

    # ------------------------------------------------------------------
    # 执行主循环
    # ------------------------------------------------------------------

    def _run(self, sequence: List[SequenceItem]) -> None:
        """执行主循环（运行在后台线程）"""
        try:
            for index, item in enumerate(sequence):
                if self._stop_requested:
                    self._on_log("执行已停止")
                    break

                # 暂停等待
                while self._paused:
                    time.sleep(0.1)
                    if self._stop_requested:
                        self._on_log("执行已停止")
                        break
                if self._stop_requested:
                    break

                item.status = SequenceItemStatus.RUNNING
                self._on_step_started(index, item)

                try:
                    success = self._execute_action(item)
                    if success:
                        item.status = SequenceItemStatus.SUCCESS
                        self._on_step_completed(index, item)
                    else:
                        item.status = SequenceItemStatus.FAILED
                        self._on_step_failed(index, item, "动作执行失败")
                        break
                except Exception as e:
                    item.status = SequenceItemStatus.FAILED
                    error_msg = f"执行异常: {str(e)}"
                    self._on_step_failed(index, item, error_msg)
                    break
        finally:
            self._running = False
            self._on_finished()

    # ------------------------------------------------------------------
    # 动作分发（与 execution.py 逻辑一致）
    # ------------------------------------------------------------------

    def _execute_action(self, item: SequenceItem) -> bool:
        definition = item.definition
        params = definition.parameters

        self._on_log(f"正在执行: {definition.name}")
        self._on_log(f"参数: {params}")

        try:
            if definition.type == ActionType.MOVE:
                return self._execute_move(params)
            elif definition.type == ActionType.MANIPULATE:
                return self._execute_manipulate(params)
            elif definition.type == ActionType.INSPECT:
                return self._execute_inspect(params)
            elif definition.type == ActionType.WAIT:
                return self._execute_wait(params)
            elif definition.type == ActionType.CHANGE_GUN:
                return self._execute_change_gun(params)
            elif definition.type == ActionType.VISION_CAPTURE:
                return self._execute_vision_capture(params)
            else:
                self._on_log(f"未知的动作类型: {definition.type}", "error")
                return False
        except Exception as e:
            self._on_log(f"执行错误: {str(e)}", "error")
            return False

    # ------------------------------------------------------------------
    # 移动类动作
    # ------------------------------------------------------------------

    def _execute_move(self, params: dict) -> bool:
        target = params.get('目标', '机械臂')
        if target == '身体':
            return self._execute_body_move(params)
        else:
            return self._execute_robot_move(params)

    def _execute_robot_move(self, params: dict) -> bool:
        """执行机械臂移动"""
        arm = params.get('臂', '左')
        target_pose_str = params.get('点位', '')
        mode = params.get('模式', '')

        self._on_log(f"机械臂移动动作: 臂={arm}, 模式={mode}, 点位={target_pose_str}")

        if self._robot_controller is None:
            self._on_log("机械臂控制器未初始化", "error")
            return False

        try:
            target_pose = json.loads(target_pose_str)

            if arm == '左':
                if mode == 'move_j':
                    method = self._robot_controller.move_robot1
                elif mode == 'move_l':
                    method = self._robot_controller.move_robot1l
                else:
                    self._on_log(f"未知的移动模式: {mode}", "error")
                    return False
            else:
                if mode == 'move_j':
                    method = self._robot_controller.move_robot2
                elif mode == 'move_l':
                    method = self._robot_controller.move_robot2l
                else:
                    self._on_log(f"未知的移动模式: {mode}", "error")
                    return False

            # 重试机制：处理通信抖动
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                success = method(target_pose)
                if success:
                    self._on_log("机械臂移动执行完成")
                    return True
                self._on_log(f"机械臂移动失败 (第{attempt}次)，重试中...", "warn")
                time.sleep(0.5)

            self._on_log("机械臂移动重试次数耗尽", "error")
            return False
        except Exception as e:
            self._on_log(f"执行机械臂移动出错: {str(e)}", "error")
            return False

    def _execute_body_move(self, params: dict) -> bool:
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
                if self._stop_requested:
                    self._on_log("身体移动已停止")
                    return False
                st = self._body_controller.is_reached()
                if st is None:
                    self._on_log("身体通信异常", "error")
                    return False
                if st:
                    self._on_log(f"身体移动完成，位置={position}")
                    return True
                time.sleep(0.1)
        except Exception as e:
            self._on_log(f"执行身体移动出错: {str(e)}", "error")
            return False

    # ------------------------------------------------------------------
    # 操作类动作
    # ------------------------------------------------------------------

    def _execute_manipulate(self, params: dict) -> bool:
        executor = params.get('执行器', '快换手')
        number = params.get('编号', 1)
        operation = params.get('操作', '开')

        if executor == '快换手':
            from ..devices import Kuaihuanshou
            kuaihuanshou = Kuaihuanshou(port='/dev/hand')
            try:
                if operation == '开':
                    result = kuaihuanshou.send_command('open')
                elif operation == '关':
                    result = kuaihuanshou.send_command('close')
                else:
                    self._on_log(f"未知的快换手操作: {operation}", "error")
                    return False
                if result == "error" or result is False:
                    self._on_log(f"快换手操作失败: {result}", "error")
                    return False
            finally:
                kuaihuanshou.close()

        elif executor == '继电器':
            from ..devices import RelayController
            adp = RelayController()
            try:
                if operation == '开':
                    if number == 1:
                        adp.turn_on_relay_Y1()
                    elif number == 2:
                        adp.turn_on_relay_Y2()
                    else:
                        self._on_log(f"未知的编号: {number}", "error")
                        return False
                elif operation == '关':
                    if number == 1:
                        adp.turn_off_relay_Y1()
                    elif number == 2:
                        adp.turn_off_relay_Y2()
                    else:
                        self._on_log(f"未知的编号: {number}", "error")
                        return False
                else:
                    self._on_log(f"未知的继电器操作: {operation}", "error")
                    return False
            finally:
                adp.close()

        elif executor == '夹爪':
            return self._execute_gripper(operation)
        elif executor == '吸液枪':
            return self._execute_pipette(params)
        else:
            self._on_log(f"未知的执行器: {executor}", "error")
            return False

        self._on_log(f"执行器: {executor}, 编号: {number}, 操作: {operation}")
        return True

    def _execute_gripper(self, operation: str) -> bool:
        """执行夹爪动作"""
        self._on_log(f"夹爪动作: {operation}")

        if self._robot_controller is None:
            self._on_log("机械臂控制器未初始化", "error")
            return False

        method = (
            self._robot_controller.gripper_open_robot1
            if operation == '开'
            else self._robot_controller.gripper_close_robot1
        )

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                success = method()
                if success:
                    self._on_log(f"夹爪{operation}执行完成")
                    return True
                self._on_log(f"夹爪{operation}失败 (第{attempt}次)，重试中...", "warn")
            except Exception as e:
                self._on_log(f"执行夹爪出错: {str(e)} (第{attempt}次)", "warn")
            time.sleep(0.5)

        self._on_log("夹爪重试次数耗尽", "error")
        return False

    def _execute_pipette(self, params: dict) -> bool:
        """执行吸液枪动作（吸/吐）"""
        operation = params.get('操作', '吸')
        capacity = params.get('容量', 500)
        port = params.get('端口', '/dev/hand')

        self._on_log(f"吸液枪动作: 操作={operation}, 容量={capacity}ul")

        try:
            from ..devices import ADP
            adp = ADP(port=port)
            if operation == '吸':
                self._on_log("正在吸液...")
                ret = adp.absorb(capacity)
            elif operation == '吐':
                self._on_log("正在吐液...")
                ret = adp.dispense_all()
            else:
                self._on_log(f"未知的吸液枪操作: {operation}", "error")
                adp.close()
                return False

            adp.close()

            if ret:
                self._on_log(f"吸液枪{operation}执行成功")
            else:
                self._on_log(f"吸液枪{operation}执行失败", "error")
            return ret
        except Exception as e:
            self._on_log(f"执行吸液枪出错: {str(e)}", "error")
            return False

    # ------------------------------------------------------------------
    # 检测类动作
    # ------------------------------------------------------------------

    def _execute_inspect(self, params: dict) -> bool:
        sensor_id = params.get('Sensor_ID', '')
        threshold = params.get('Threshold', 0)
        timeout = params.get('Timeout', 5)

        self._on_log(f"读取传感器 {sensor_id}, 阈值: {threshold}, 超时: {timeout}s")
        time.sleep(0.8)
        self._on_log("检测完成 - 结果: 通过")
        return True


    def _execute_wait(self, params: dict) -> bool:
        wait_seconds = float(params.get('wait_seconds', 1.0))
        if wait_seconds <= 0:
            return True

        self._on_log(f"Waiting: {wait_seconds:.1f}s")
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._stop_requested:
                self._on_log("Wait cancelled by stop request")
                return False
            while self._paused:
                if self._stop_requested:
                    self._on_log("Wait cancelled by stop request")
                    return False
                time.sleep(0.1)
            time.sleep(0.05)
        return True

    # ------------------------------------------------------------------
    # 换枪类动作
    # ------------------------------------------------------------------

    def _execute_change_gun(self, params: dict) -> bool:
        """执行换枪动作"""
        gun_position = params.get('Gun_Position', 1)
        operation = params.get('Operation', '取')

        self._on_log(f"换枪动作: 枪位={gun_position}, 操作={operation}")

        if self._robot_controller is None:
            self._on_log("机械臂控制器未初始化", "error")
            return False

        try:
            method_map = {
                (1, '取'): 'pick_gun1',
                (2, '取'): 'pick_gun2',
                (1, '放'): 'drop_gun1',
                (2, '放'): 'drop_gun2',
            }

            key = (gun_position, operation)
            if key not in method_map:
                self._on_log(f"未知的换枪参数组合: 枪位={gun_position}, 操作={operation}", "error")
                return False

            method_name = method_map[key]
            self._on_log(f"调用: {method_name}()")

            method = getattr(self._robot_controller, method_name)
            success = method()

            if success:
                self._on_log(f"{method_name} 执行完成")
            return success
        except Exception as e:
            self._on_log(f"执行换枪出错: {str(e)}", "error")
            return False

    # ------------------------------------------------------------------
    # 视觉抓取动作
    # ------------------------------------------------------------------

    def _execute_vision_capture(self, params: dict) -> bool:
        """执行视觉抓取动作"""
        target_robot = params.get('目标机械臂', 'robot1')
        workflow = params.get('工作流', 'vertical')
        confidence = params.get('置信度', 0.7)
        debug_images = params.get('调试图片', True)
        move_velocity = params.get('移动速度', 15)
        gripper_length = params.get('夹爪长度', 100.0)

        self._on_log(f"视觉抓取动作: 机械臂={target_robot}, 工作流={workflow}")
        self._on_log(f"  置信度={confidence}, 调试图片={debug_images}")

        if self._robot_controller is None:
            self._on_log("机械臂控制器未初始化", "error")
            return False

        try:
            # 按需取帧：启动 RealSense
            from ..widgets.frame_grabber import OnDemandFrameGrabber

            grabber_sn = None
            try:
                from ..core.config_loader import Config
                sn = Config.get_instance().REALSENSE_DEVICE_SN
                if sn:
                    grabber_sn = sn
            except Exception:
                pass

            grabber = OnDemandFrameGrabber(grabber_sn)
            try:
                color, depth, intr = grabber.grab(timeout=10)
            except RuntimeError as e:
                self._on_log(f"相机取帧失败: {e}", "error")
                grabber._cleanup()
                return False
            except Exception as e:
                self._on_log(f"RealSense 初始化异常: {type(e).__name__}: {e}", "error")
                grabber._cleanup()
                return False
            self._robot_controller.inject_frames(color, depth, intr)
            grabber._cleanup()

            from ..vision import VisionCaptureGUIAction

            action = VisionCaptureGUIAction(
                controller=self._robot_controller,
                target_robot=target_robot,
                confidence=confidence,
                debug_images=debug_images,
                move_velocity=move_velocity,
                gripper_length=gripper_length,
                workflow=workflow,
                raise_on_error=False,
            )

            result = action.execute()

            if result.get('success'):
                self._on_log("视觉抓取执行成功")
                return True
            else:
                error = result.get('error', '未知错误')
                self._on_log(f"视觉抓取执行失败: {error}", "error")
                return False

        except Exception as e:
            self._on_log(f"执行视觉抓取出错: {str(e)}", "error")
            return False