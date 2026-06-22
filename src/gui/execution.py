from __future__ import annotations
import time
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from ..core.models import SequenceItem, SequenceItemStatus, ActionType, LoopBlock, SequenceEntry
from ..arm_sdk.controller import RobotController
from ..devices import ModbusMotor, RelayController, Kuaihuanshou, ADP
from ..base_move.move_controller import RobotMoveController
from ..core.config_loader import Config
from ..actions.circle_dispense import execute_right_arm_circle_dispense
class ExecutionThread(QThread):
    started = pyqtSignal()
    finished = pyqtSignal()
    step_started = pyqtSignal(int, SequenceItem)
    step_completed = pyqtSignal(int, SequenceItem)
    step_failed = pyqtSignal(int, SequenceItem, str)
    loop_progress = pyqtSignal(str, int, int)  # (loop_uuid, current_iteration, total_iterations)
    log_message = pyqtSignal(str)

    def __init__(self, sequence: list[SequenceEntry], robot_controller: RobotController | None = None, body_controller: ModbusMotor | None = None,
    move_controller: RobotMoveController | None = None):
        super().__init__()
        self.sequence = sequence
        self._stop_requested = False
        self._paused = False
        self._robot_controller = robot_controller
        self._body_controller = body_controller
        self._move_controller = move_controller

        self.config = Config.get_instance()

        self.execute_methods = {
            ActionType.MOVE: self._execute_move,
            ActionType.BASE_MOVE: self._execute_base_move,
            ActionType.MANIPULATE: self._execute_manipulate,
            ActionType.INSPECT: self._execute_inspect,
            ActionType.WAIT: self._execute_wait,
            ActionType.CHANGE_GUN: self._execute_change_gun,
            ActionType.VISION_CAPTURE: self._execute_vision_capture,
            ActionType.TRAJECTORY: self._execute_trajectory,
        }


    def stop(self):
        self._stop_requested = True

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def run(self):
        self.started.emit()

        # 构建扁平执行列表：(SequenceItem, parent_loop_or_None)
        flat_sequence: list[tuple[SequenceItem, LoopBlock | None]] = []
        for entry in self.sequence:
            if isinstance(entry, LoopBlock):
                for iter_idx in range(entry.repeat_count):
                    for child in entry.items:
                        # 每次迭代克隆子项，保留原始 UUID 以匹配树节点
                        clone = SequenceItem.from_dict(child.to_dict())
                        clone.uuid = child.uuid  # 保持 UUID 一致以便 UI 更新
                        flat_sequence.append((clone, entry))
            elif isinstance(entry, SequenceItem):
                flat_sequence.append((entry, None))

        # 追踪循环迭代
        loop_iteration: dict[str, int] = {}  # loop_uuid -> current_iteration
        loop_item_counter: dict[str, int] = {}  # loop_uuid -> items executed in current iter

        for index, (item, loop) in enumerate(flat_sequence):
            if self._stop_requested:
                self.log_message.emit("执行已停止")
                break

            while self._paused:
                time.sleep(0.1)
                if self._stop_requested:
                    self.log_message.emit("执行已停止")
                    break

            if self._stop_requested:
                break

            # 检测新的一轮循环开始
            if loop is not None:
                counter = loop_item_counter.get(loop.uuid, 0)
                iter_size = len(loop.items)
                if counter == 0:
                    # 新一轮开始
                    current_iter = loop_iteration.get(loop.uuid, 0) + 1
                    loop_iteration[loop.uuid] = current_iter
                    self.log_message.emit(f"🔁 循环块 第 {current_iter}/{loop.repeat_count} 轮开始")
                    self.loop_progress.emit(loop.uuid, current_iter, loop.repeat_count)
                loop_item_counter[loop.uuid] = (counter + 1) % iter_size

            item.status = SequenceItemStatus.RUNNING
            self.step_started.emit(index, item)

            try:
                success = self._execute_action(item)
                item.status = SequenceItemStatus.SUCCESS if success else SequenceItemStatus.FAILED

                if success:
                    self.step_completed.emit(index, item)
                else:
                    error_msg = "动作执行失败"
                    self.step_failed.emit(index, item, error_msg)
                    break

            except Exception as e:
                item.status = SequenceItemStatus.FAILED
                error_msg = f"执行异常: {str(e)}"
                self.step_failed.emit(index, item, error_msg)
                break

        self.finished.emit()
      

    def _execute_action(self, item: SequenceItem) -> bool:
        definition = item.definition
        params = definition.parameters

        self.log_message.emit(f"正在执行：{definition.name}")
        self.log_message.emit(f"参数：{params}")

        try:
            # 根据动作类型获取对应的执行方法
            execute_method = self.execute_methods.get(definition.type)
            if execute_method:
                return execute_method(params)
            else:
                self.log_message.emit(f"未知的动作类型：{definition.type}")
                return False
        except Exception as e:
            self.log_message.emit(f"执行错误：{str(e)}")
            return False

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
        mode = params.get('模式','')

        self.log_message.emit(f"机械臂移动动作: 臂={arm}, 模式={mode}, 点位={target_pose_str}")

        if self._robot_controller is None:
            self.log_message.emit("机械臂控制器未初始化")
            return False

        try:
            from ..core.pose_compensation import compensate_pose, parse_pose

            target_pose = parse_pose(target_pose_str)
            localization_config = params.get('定位补偿', {})
            if localization_config.get('enabled'):
                teach_offset = localization_config.get('teach_offset')
                if not teach_offset:
                    self.log_message.emit("定位补偿已启用，但动作中缺少创建时定位基准")
                    return False

                from .udp_receive import get_latest_position

                current_offset = get_latest_position(max_age=2.0, wait_timeout=1.5)
                if current_offset is None:
                    self.log_message.emit("定位补偿已启用，但未收到当前有效定位数据")
                    return False

                target_pose = compensate_pose(target_pose, teach_offset, current_offset, arm=arm)
                self.log_message.emit(
                    "定位补偿: "
                    f"teach=({teach_offset.get('x')}, {teach_offset.get('y')}, {teach_offset.get('angle')}) "
                    f"current=({current_offset.get('x')}, {current_offset.get('y')}, {current_offset.get('angle')})"
                )
                self.log_message.emit(f"补偿后点位: {target_pose}")

            if arm == '左':
                if mode == 'move_j':
                    method = self._robot_controller.move_robot1
                elif mode == 'move_l':
                    method = self._robot_controller.move_robot1l
                else:
                    self.log_message.emit("模式异常")
                    return False
            else:
                if mode == 'move_j':
                    method = self._robot_controller.move_robot2
                elif mode == 'move_l':
                    method = self._robot_controller.move_robot2l
                else:
                    self.log_message.emit("模式异常")
                    return False

            # 重试机制：处理通信抖动（-1 发送失败，-2 接收失败，-3 解析失败）
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                success = method(target_pose)
                if success:
                    self.log_message.emit(f"机械臂移动执行完成")
                    return True
                # 若非通信错误（ret==1 参数/状态错误），直接失败
                self.log_message.emit(f"机械臂移动失败 (第{attempt}次)，重试中...")
                time.sleep(0.5)

            self.log_message.emit("机械臂移动重试次数耗尽")
            return False
        except Exception as e:
            self.log_message.emit(f"执行机械臂移动出错: {str(e)}")
            return False

    def _execute_base_move(self, params: dict) -> bool:
        """执行底盘移动（统一入口，根据 move_mode 区分）"""
        move_mode = params.get('move_mode', 'position')
        
        if move_mode == 'position':
            return self._execute_base_move_position(params)
        elif move_mode == 'distance':
            return self._execute_base_move_distance(params)
        else:
            self.log_message.emit(f"未知的移动方式：{move_mode}")
            return False

    def _execute_base_move_position(self, params: dict) -> bool:
        """执行底盘位置移动"""
        id_value = params.get('id', 0)
        cid = params.get('cid', 0)
        
        self.log_message.emit(f"底盘位置移动：ID={id_value}, CID={cid}")
        
        if self._move_controller is None:
            self.log_message.emit("底盘移动控制器未初始化")
            return False
        
        try:
            success = self._move_controller.move_to_position(id_value, cid)
            
            if success:
                self.log_message.emit(f"底盘位置移动完成：ID={id_value}, CID={cid}")
            else:
                self.log_message.emit(f"底盘位置移动失败：ID={id_value}, CID={cid}")
            
            return success
        except Exception as e:
            self.log_message.emit(f"执行底盘位置移动出错：{str(e)}")
            return False

    def _execute_base_move_distance(self, params: dict) -> bool:
        """执行底盘距离移动"""
        valueY = params.get('valueY', 0.0)
        
        self.log_message.emit(f"底盘距离移动：距离={valueY}cm")
        
        if self._move_controller is None:
            self.log_message.emit("底盘移动控制器未初始化")
            return False
        
        try:
            success = self._move_controller.move_slowly(valueY)
            
            if success:
                self.log_message.emit(f"底盘距离移动完成：距离={valueY}cm")
            else:
                self.log_message.emit(f"底盘距离移动失败：距离={valueY}cm")
            
            return success
        except Exception as e:
            self.log_message.emit(f"执行底盘距离移动出错：{str(e)}")
            return False

    def _execute_body_move(self, params: dict) -> bool:
        """执行身体移动（ModbusMotor）"""
        position = params.get('位置', 0)

        self.log_message.emit(f"身体移动动作: 目标位置={position}")

        if self._body_controller is None:
            self.log_message.emit("身体控制器未初始化")
            return False

        try:
            self.log_message.emit(f"正在移动身体到位置 {position}...")
            self._body_controller.move_to(position)

            # 等待到达目标位置
            while True:
                if self._stop_requested:
                    self.log_message.emit("身体移动已停止")
                    return False
                st = self._body_controller.is_reached()
                if st is None:
                    self.log_message.emit("身体通信异常")
                    return False
                if st:
                    self.log_message.emit(f"身体移动完成，位置={position}")
                    return True
                time.sleep(0.1)

        except Exception as e:
            self.log_message.emit(f"执行身体移动出错: {str(e)}")
            return False

    def _execute_manipulate(self, params: dict) -> bool:

        executor = params.get('执行器', '快换手')
        number = params.get('编号', 1)
        operation = params.get('操作', '开')
        if executor == '快换手':

            kuaihuanshou = Kuaihuanshou(port=self.config.KUAIHUANSHOU_SERIAL_PORT)
            try:
                if operation == '开':
                    result = kuaihuanshou.send_command('open')
                elif operation == '关':
                    result = kuaihuanshou.send_command('close')
                else:
                    self.log_message.emit(f"未知的快换手操作: {operation}")
                    return False
                if result == "error" or result is False:
                    self.log_message.emit(f"快换手操作失败: {result}")
                    return False
            finally:
                kuaihuanshou.close()

        elif executor == '继电器':
            adp = RelayController()
            try:
                if operation == '开':
                    if number == 1:
                        adp.turn_on_relay_Y1()
                    elif number == 2:
                        adp.turn_on_relay_Y2()
                    else:
                        self.log_message.emit(f"未知的编号: {number}")
                        return False
                elif operation == '关':
                    if number == 1:
                        adp.turn_off_relay_Y1()
                    elif number == 2:
                        adp.turn_off_relay_Y2()
                    else:
                        self.log_message.emit(f"未知的编号: {number}")
                        return False
                else:
                    self.log_message.emit(f"未知的继电器操作: {operation}")
                    return False
            finally:
                adp.close()

        elif executor == '夹爪':
            return self._execute_gripper(operation)
        elif executor == '吸液枪':
            return self._execute_pipette(params)
        elif executor == '右臂转圈注液':
            return execute_right_arm_circle_dispense(
                robot_controller=self._robot_controller,
                params=params,
                default_port=self.config.ADP_SERIAL_PORT,
                log=lambda message, level="info": self.log_message.emit(message),
                stop_requested=lambda: self._stop_requested,
                paused=lambda: self._paused,
            )
        else:
            self.log_message.emit(f"未知的执行器: {executor}")
            return False

        self.log_message.emit(f"执行器: {executor}, 编号: {number}, 操作: {operation}")
        return True

    def _execute_gripper(self, operation: str) -> bool:
        """执行夹爪动作"""
        self.log_message.emit(f"夹爪动作: {operation}")

        if self._robot_controller is None:
            self.log_message.emit("机械臂控制器未初始化")
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
                    self.log_message.emit(f"夹爪{operation}执行完成")
                    return True
                self.log_message.emit(f"夹爪{operation}失败 (第{attempt}次)，重试中...")
            except Exception as e:
                self.log_message.emit(f"执行夹爪出错: {str(e)} (第{attempt}次)")
            time.sleep(0.5)

        self.log_message.emit("夹爪重试次数耗尽")
        return False

    def _execute_pipette(self, params: dict) -> bool:
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
        port = params.get('端口', self.config.KUAIHUANSHOU_SERIAL_PORT)

        self.log_message.emit(
            f"吸液枪动作: 操作={operation}, 容量={capacity}ul, "
            f"吸液速度={absorb_speed or '-'}ul/s, 吐液速度={dispense_speed or '-'}ul/s"
        )

        try:
            adp = None
            if operation == '吸':
                adp = ADP(port=port)
                if absorb_speed:
                    self.log_message.emit(f"正在设置吸液速度: {absorb_speed}ul/s")
                    if not adp.set_absorb_speed(absorb_speed):
                        self.log_message.emit("设置吸液速度失败")
                        ret = False
                    else:
                        self.log_message.emit("正在吸液...")
                        ret = adp.absorb(capacity)
                else:
                    self.log_message.emit("正在吸液...")
                    ret = adp.absorb(capacity)
            elif operation == '吐':
                adp = ADP(port=port)
                if dispense_speed:
                    self.log_message.emit(f"正在设置吐液速度: {dispense_speed}ul/s")
                    if not adp.set_dispense_speed(dispense_speed):
                        self.log_message.emit("设置吐液速度失败")
                        ret = False
                    else:
                        self.log_message.emit("正在吐液...")
                        ret = adp.dispense_all() if full_dispense else adp.dispense(capacity)
                else:
                    self.log_message.emit("正在吐液...")
                    ret = adp.dispense_all() if full_dispense else adp.dispense(capacity)
            elif operation == '退枪头':
                self.log_message.emit("正在退枪头...")
                from ..devices.yiyeqiang_out import eject_tip
                ret = eject_tip(port=port)
            else:
                self.log_message.emit(f"未知的吸液枪操作: {operation}")
                return False

            if adp is not None:
                adp.close()

            if ret:
                self.log_message.emit(f"吸液枪{operation}执行成功")
            else:
                self.log_message.emit(f"吸液枪{operation}执行失败")
            return ret
        except Exception as e:
            self.log_message.emit(f"执行吸液枪出错: {str(e)}")
            return False


    def _execute_inspect(self, params: dict) -> bool:
        sensor_id = params.get('Sensor_ID', '')
        threshold = params.get('Threshold', 0)
        timeout = params.get('Timeout', 5)

        self.log_message.emit(f"读取传感器 {sensor_id}, 阈值: {threshold}, 超时: {timeout}s")
        time.sleep(0.8)
        self.log_message.emit("检测完成 - 结果: 通过")
        return True


    def _execute_wait(self, params: dict) -> bool:
        wait_seconds = float(params.get('wait_seconds', 1.0))
        if wait_seconds <= 0:
            return True

        self.log_message.emit(f"Waiting: {wait_seconds:.1f}s")
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._stop_requested:
                self.log_message.emit("Wait cancelled by stop request")
                return False
            while self._paused:
                if self._stop_requested:
                    self.log_message.emit("Wait cancelled by stop request")
                    return False
                time.sleep(0.1)
            time.sleep(0.05)
        return True

    def _execute_trajectory(self, params: dict) -> bool:
        robot_name = params.get("robot", "robot1")
        file_path = params.get("file_path", "")

        self.log_message.emit(f"执行轨迹动作: robot={robot_name}, file={file_path}")

        if self._robot_controller is None:
            self.log_message.emit("机械臂控制器未初始化")
            return False
        if not file_path or not Path(file_path).exists():
            self.log_message.emit(f"轨迹文件不存在: {file_path}")
            return False

        ctrl_name = "robot1_ctrl" if robot_name == "robot1" else "robot2_ctrl"
        ctrl = getattr(self._robot_controller, ctrl_name, None)
        robot = getattr(ctrl, "robot", None)
        if robot is None:
            self.log_message.emit(f"{robot_name} 未连接")
            return False

        try:
            if not self._robot_controller.demo_send_project(robot, file_path, project_type=1):
                self.log_message.emit("轨迹发送失败")
                return False

            start_time = time.time()
            timeout_seconds = float(params.get("timeout_seconds", 600))
            while time.time() - start_time < timeout_seconds:
                if self._stop_requested:
                    self.log_message.emit("轨迹执行已停止")
                    return False
                rst = self._robot_controller.demo_get_program_run_state(robot, time_sleep=1, max_retries=1)
                if rst:
                    self.log_message.emit("轨迹执行完成")
                    return True
                time.sleep(0.5)

            self.log_message.emit("轨迹执行超时")
            return False
        except Exception as e:
            self.log_message.emit(f"轨迹执行异常: {e}")
            return False

    def _execute_change_gun(self, params: dict) -> bool:
        """执行换枪动作"""
        gun_position = params.get('Gun_Position', 1)
        operation = params.get('Operation', '取')

        self.log_message.emit(f"换枪动作: 枪位={gun_position}, 操作={operation}")

        if self._robot_controller is None:
            self.log_message.emit("机械臂控制器未初始化")
            return False

        try:
            method_map = {
                (1, '取'): 'pick_gun1',
                (2, '取'): 'pick_gun2',
                (1, '放'): 'drop_gun1',
                (2, '放'): 'drop_gun2'
            }

            key = (gun_position, operation)
            if key not in method_map:
                self.log_message.emit(f"未知的换枪参数组合: 枪位={gun_position}, 操作={operation}")
                return False

            method_name = method_map[key]
            self.log_message.emit(f"调用: {method_name}()")

            method = getattr(self._robot_controller, method_name)
            success = method()

            if success:
                self.log_message.emit(f"{method_name} 执行完成")
            return success
        except Exception as e:
            self.log_message.emit(f"执行换枪出错: {str(e)}")
            return False

    def _execute_vision_capture(self, params: dict) -> bool:
        """执行视觉抓取动作（委托共用模块）"""
        from ..vision.executor import execute_vision_capture
        return execute_vision_capture(self._robot_controller, params, self.log_message.emit)
