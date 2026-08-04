import time
import os
import threading
from Robotic_Arm.rm_robot_interface import RoboticArm, rm_send_project_t, rm_thread_mode_e


class SimpleRobotArm:
    def __init__(self, robot_config, robot_name="Robot"):
        """
        简化的机械臂控制器
        :param robot_config: 机器人配置字典，包含ip、port等
        :param robot_name: 机器人名称，用于日志输出
        """
        self.robot_config = robot_config
        self.robot_name = robot_name
        self.robot = None
        self.handle = None
        self.is_connected = False
        self.last_error = None
        self.sdk_lock = threading.RLock()

    def connect(self):
        """连接到机械臂"""
        try:
            if self.robot is None:
                print(f"\n==== 初始化{self.robot_name} ====")
                self.robot = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
                self.handle = self.robot.rm_create_robot_arm(
                    self.robot_config["ip"],
                    self.robot_config["port"]
                )

                # 检查连接状态
                ret, state = self.robot.rm_get_current_arm_state()
                if ret != 0:
                    raise Exception(f"获取{self.robot_name}状态失败，错误码：{ret}")

                if state.get('error_code', 0) != 0:
                    raise Exception(f"{self.robot_name}存在错误，错误码：{state['error_code']}")

                self.is_connected = True
                print(f"{self.robot_name}连接成功")
                print(f"{self.robot_name}当前状态: {state}")

        except Exception as e:
            self.last_error = str(e)
            print(f"{self.robot_name}连接失败: {e}")
            self.is_connected = False
            self.disconnect()
            raise

    def disconnect(self):
        """断开机械臂连接"""
        with self.sdk_lock:
            if self.robot is not None:
                try:
                    if self.handle is not None:
                        self.robot.rm_delete_robot_arm()
                except Exception as e:
                    print(f"{self.robot_name}断开连接时出错: {e}")
                    self.last_error = str(e)
                finally:
                    self.robot = None
                    self.handle = None
                    self.is_connected = False
                    print(f"{self.robot_name}连接已断开")

    def owns_robot(self, robot) -> bool:
        return self.robot is robot

class RobotController:
    
    def __init__(self, robot1_config, robot2_config):
        # 初始化并连接双机械臂
        self.robot1_ctrl = SimpleRobotArm(robot1_config, "Robot1")
        self.robot2_ctrl = SimpleRobotArm(robot2_config, "Robot2")

        try:
            self.robot1_ctrl.connect()
            self.robot2_ctrl.connect()
        except Exception as exc:
            self.robot1_ctrl.disconnect()
            self.robot2_ctrl.disconnect()
            raise RuntimeError("机械臂连接失败，请检查网络或供电") from exc

    def _arm_controller(self, arm):
        if arm == "left":
            controller = self.robot1_ctrl
        elif arm == "right":
            controller = self.robot2_ctrl
        else:
            raise ValueError(f"unsupported arm: {arm}")
        if controller.robot is None or not controller.is_connected:
            raise RuntimeError(f"{arm} arm is not connected")
        return controller

    def stop_arm(self, arm, *, emergency):
        controller = self._arm_controller(arm)
        method = (
            controller.robot.rm_set_arm_stop
            if emergency
            else controller.robot.rm_set_arm_slow_stop
        )
        return int(method())

    def move_to_pose(
        self,
        arm,
        pose,
        *,
        linear,
        velocity,
        blend_radius,
        connected,
        blocking,
    ):
        controller = self._arm_controller(arm)
        method = controller.robot.rm_movel if linear else controller.robot.rm_movej_p
        with controller.sdk_lock:
            return int(method(
                pose,
                v=velocity,
                r=blend_radius,
                connect=int(connected),
                block=int(blocking),
            ))

    def read_state(self, arm, *, blocking=True):
        controller = self._arm_controller(arm)
        if not controller.sdk_lock.acquire(blocking=blocking):
            return None
        try:
            return controller.robot.rm_get_current_arm_state()
        finally:
            controller.sdk_lock.release()

    def read_telemetry(self, arm, *, blocking=True):
        controller = self._arm_controller(arm)
        if not controller.sdk_lock.acquire(blocking=blocking):
            return None
        try:
            robot = controller.robot
            return {
                "state": robot.rm_get_current_arm_state(),
                "gripper": self._optional_sdk_call(robot, "rm_get_gripper_state"),
                "joint_currents": self._optional_sdk_call(
                    robot,
                    "rm_get_current_joint_current",
                ),
                "wrench": self._optional_sdk_call(robot, "rm_get_force_data"),
            }
        finally:
            controller.sdk_lock.release()

    @staticmethod
    def _optional_sdk_call(robot, method_name):
        method = getattr(robot, method_name, None)
        return method() if callable(method) else None

    def release_gripper(self, arm, *, speed, timeout_s):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            return int(controller.robot.rm_set_gripper_release(
                speed=speed,
                block=True,
                timeout=timeout_s,
            ))

    def grip(self, arm, *, speed, force, timeout_s):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            return int(controller.robot.rm_set_gripper_pick_on(
                speed=speed,
                block=True,
                timeout=timeout_s,
                force=force,
            ))

    def set_gripper_position(self, arm, position, *, timeout_s):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            return int(controller.robot.rm_set_gripper_position(
                position,
                block=True,
                timeout=timeout_s,
            ))

    def follow_joints(self, arm, joints, *, follow, trajectory_mode):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            return int(controller.robot.rm_movej_canfd(
                joints,
                follow,
                trajectory_mode=trajectory_mode,
            ))

    def initialize_joints(
        self,
        arm,
        joints,
        *,
        velocity,
        radius,
        connected,
        blocking,
    ):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            return int(controller.robot.rm_movej(
                joints,
                velocity,
                radius,
                int(connected),
                int(blocking),
            ))

    def set_drag_teaching(self, arm, *, enabled):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            if enabled:
                return int(controller.robot.rm_start_drag_teach(1))
            return int(controller.robot.rm_stop_drag_teach())

    def save_trajectory(self, arm, path):
        controller = self._arm_controller(arm)
        with controller.sdk_lock:
            return controller.robot.rm_save_trajectory(path)

    def send_trajectory(self, arm, path):
        controller = self._arm_controller(arm)
        return self.demo_send_project(controller.robot, path, project_type=1)

    def is_trajectory_complete(self, arm):
        controller = self._arm_controller(arm)
        return self.demo_get_program_run_state(
            controller.robot,
            time_sleep=0,
            max_retries=1,
        )

    def _sdk_lock_for_robot(self, robot):
        for ctrl in (self.robot1_ctrl, self.robot2_ctrl):
            if ctrl.owns_robot(robot):
                return ctrl.sdk_lock
        return threading.RLock()

    def demo_send_project(self, robot, file_path, plan_speed=20, only_save=0, save_id=0, step_flag=0, auto_start=0, project_type=1):
        """向机械臂发送项目"""
        if not file_path:
            print("文件路径为空")
            return False

        file_path = os.path.abspath(os.fspath(file_path))
        if not os.path.isfile(file_path):
            print("文件路径不存在或不是文件:", file_path)
            return False

        path_bytes = file_path.encode("utf-8")
        if len(path_bytes) >= 300:
            print(f"项目路径过长: {len(path_bytes)} bytes，SDK 限制小于 300 bytes")
            print("请把轨迹文件移动到更短的英文路径后重试:", file_path)
            return False

        file_size = os.path.getsize(file_path)
        if file_size <= 0:
            print("项目文件为空:", file_path)
            return False

        with self._sdk_lock_for_robot(robot):
            try:
                ret, state = robot.rm_get_current_arm_state()
            except Exception as exc:
                print("发送项目前读取机械臂状态异常:", exc)
                return False

            if ret != 0:
                print(f"发送项目前读取机械臂状态失败, 错误代码: {ret}")
                print("通常表示与控制器通信异常，请检查机器人上电、网线/IP、控制器是否被其他程序占用。")
                return False

            state_error = state.get("error_code", 0) if isinstance(state, dict) else 0
            if state_error != 0:
                print(f"机械臂当前存在错误, error_code: {state_error}")
                print("请先在示教器或控制器端清除机械臂错误后再下发项目。")
                return False

            print(
                "发送项目参数:",
                f"path={file_path}",
                f"size={file_size}",
                f"plan_speed={plan_speed}",
                f"only_save={only_save}",
                f"save_id={save_id}",
                f"step_flag={step_flag}",
                f"auto_start={auto_start}",
                f"project_type={project_type}",
            )

            send_project = rm_send_project_t(file_path, plan_speed, only_save, save_id, step_flag, auto_start, project_type)
            result = robot.rm_send_project(send_project)

        if result[0] == 0:
            if result[1] == -1:
                print("项目发送并运行成功")
                return True
            elif result[1] == 0:
                print("项目发送成功但未运行,数据长度验证失败")
                return False
            else:
                print("项目发送成功但运行失败,问题项目行数:", result[1])
                return False
        else:
            print("发送项目失败,错误代码:", result[0])
            if result[0] == 1:
                print("错误代码 1 一般是控制器通信/响应失败；若 SDK 同时打印 project_state: false，请重点检查文件类型是否匹配、网络连接和控制器当前状态。")
                print("拖动示教轨迹应使用 project_type=1；在线编程文件应使用 project_type=0。")
            return False

    def demo_get_program_run_state(self, robot, time_sleep=1, max_retries=10):
        """获取程序运行状态"""
        retries = 0
        while retries < max_retries:
            time.sleep(time_sleep)
            with self._sdk_lock_for_robot(robot):
                result = robot.rm_get_program_run_state()

            if result[0] == 0:
                print("程序运行状态:", result[1])
                run_state = result[1]['run_state']
                if run_state == 0:
                    print("程序已结束")
                    return True
            else:
                return False

            retries += 1

        if retries == max_retries:
            print("达到最大查询次数,退出")
            return False

    def shutdown(self):
        """断开与机械臂的连接"""
        if hasattr(self, "robot1_ctrl") and self.robot1_ctrl is not None:
            self.robot1_ctrl.disconnect()
        if hasattr(self, "robot2_ctrl") and self.robot2_ctrl is not None:
            self.robot2_ctrl.disconnect()
