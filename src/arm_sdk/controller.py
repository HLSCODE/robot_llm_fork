import cv2
import time
import numpy as np
from ultralytics import YOLO, SAM
from ..vision.interface import vertical_catch
import os
from sklearn.mixture import GaussianMixture
import socket
import pickle
import struct
from Robotic_Arm.rm_robot_interface import *

# 先确保配置已加载，再导入配置值
from .config import ensure_config_loaded
ensure_config_loaded()  # 在导入具体配置前确保已加载
from .config import *  # 导入配置值
from ..devices.kuaihuanshou import Kuaihuanshou
from ..devices.relay import RelayController
from ..devices.adp import ADP
from ..devices import yiyeqiang_out
from ..core.config_loader import Config


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
            raise

    def disconnect(self):
        """断开机械臂连接"""
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


class RobotController:
    
    def __init__(self):
        # 初始化并连接双机械臂
        self.robot1_ctrl = SimpleRobotArm(ROBOT1_CONFIG, "Robot1")
        self.robot2_ctrl = SimpleRobotArm(ROBOT2_CONFIG, "Robot2")

        try:
            self.robot1_ctrl.connect()
            self.robot2_ctrl.connect()
        except Exception as exc:
            raise RuntimeError("机械臂连接失败，请检查网络或供电") from exc

        # 快换手点位
        self.TOOL_POINTS = [
            {"point_name": "huandian1", "pose": [-374.88 / 1000, 325.69 / 1000, -70 / 1000, -3.141, -0.0, -0.278]},  
            {"point_name": "huandian2", "pose": [-135.95 / 1000, 324.0 / 1000, -70 / 1000, -3.141, -0.0, -0.278]},  
            {"point_name": "huandian3", "pose": [-136.08 / 1000, 324.0 / 1000, -96.15 / 1000, -3.141, -0.026, -0.278]},   
        ]
        self.gripper_offset = [3.146, 0, 3.128]  # 侧装的垂直姿态
        self.translation_vector = [-0.10273135, 0.03312807, -0.07214614]

        # 按需帧注入槽（由 OnDemandFrameGrabber 填充）
        self._injected_color = None
        self._injected_depth = None
        self._injected_intr = None

        config = Config.get_instance()

        # 加载模型
        self.yolo_model = YOLO(config.YOLO_MODEL_PATH)
        self.sam_model = SAM(config.SAM_MODEL_PATH)

        # 手眼标定参数
        self.rotation_matrix = [[0.00215684,0.97503835,0.22202606], 
                              [-0.99995231,-0.0000119, 0.00976617],
                              [0.00952503, -0.22203654, 0.97499182]]

    def process_mask_with_gmm(self, image, mask, n_components=1):
        """使用高斯混合模型(GMM)处理图像分割掩码"""
        masked_image = cv2.bitwise_and(image, image, mask=mask)
        y_coords, x_coords = np.nonzero(mask)
        pixels = masked_image[y_coords, x_coords]
        
        if len(pixels) == 0:
            return mask

        features = np.column_stack((x_coords, y_coords, pixels))
        gmm = GaussianMixture(n_components=n_components, random_state=42)
        labels = gmm.fit_predict(features)
        
        new_mask = np.zeros_like(mask)
        for i in range(n_components):
            component_mask = np.zeros_like(mask)
            component_indices = (labels == i)
            component_mask[y_coords[component_indices], x_coords[component_indices]] = 255
            
            num_labels, labels_im = cv2.connectedComponents(component_mask)
            if num_labels > 1:
                largest_label = 1 + np.argmax([np.sum(labels_im == i) for i in range(1, num_labels)])
                component_mask = (labels_im == largest_label).astype(np.uint8) * 255
            
            new_mask = cv2.bitwise_or(new_mask, component_mask)
        
        kernel = np.ones((5,5), np.uint8)
        new_mask = cv2.morphologyEx(new_mask, cv2.MORPH_CLOSE, kernel)
        new_mask = cv2.morphologyEx(new_mask, cv2.MORPH_OPEN, kernel)
        
        return new_mask

    def inject_frames(self, color, depth, intrinsics):
        """注入帧数据，供 get_frames_from_gui() 直接返回（绕过 socket）。"""
        self._injected_color = color
        self._injected_depth = depth
        self._injected_intr = intrinsics

    def get_frames_from_gui(self, max_retries=3, timeout=5):
        """从GUI获取帧数据，优先返回 inject_frames 注入的数据。"""
        # 优先返回已注入的帧数据（由 OnDemandFrameGrabber 填充）
        if self._injected_color is not None:
            color = self._injected_color
            depth = self._injected_depth
            intr = self._injected_intr
            self._injected_color = None  # 用完清空，防止复用
            return color, depth, intr

        retries = 0
        while retries < max_retries:
            try:
                client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client_socket.settimeout(timeout)
                client_socket.connect(('localhost', 12345))
                
                client_socket.send("get_frames".encode())
                data_size = struct.unpack(">L", client_socket.recv(4))[0]
                
                received_data = b""
                while len(received_data) < data_size:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    received_data += data
                
                frames_data = pickle.loads(received_data)
                return frames_data['color'], frames_data['depth'], frames_data['intrinsics']
            
            except (socket.timeout, ConnectionRefusedError) as e:
                print(f"连接尝试 {retries + 1} 失败: {e}")
                retries += 1
                time.sleep(1)
            except Exception as e:
                print(f"获取帧错误：{e}")
                return None, None, None
            finally:
                client_socket.close()
        
        print("达到最大重试次数，无法获取帧")
        return None, None, None

    def capture_and_move(self, robot, width=640, height=480, fps=60):
        """捕获图像并执行移动"""
        try:
            ret = robot.rm_set_gripper_release(speed=100, block=True, timeout=3)
            ret, initial_state = robot.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取初始位姿失败，错误码：{ret}")
            
            initial_pose = initial_state['pose']
            print("/n==== 初始位姿 ====")
            print("初始位姿:", initial_pose)

            # 获取图像数据并处理
            color_image, depth_image, color_intr = self.get_frames_from_gui()
            if color_image is None or depth_image is None or color_intr is None:
                raise Exception("无法获取图像或相机内参")
            
            # 保存原始图像
            cv2.imwrite('/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/pictures/original_image.jpg', 
                        cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR))
            
            # YOLO检测获取边界框
            yolo_results = self.yolo_model(color_image, verbose=False)
            
            # 创建掩码图像
            mask = np.zeros((height, width), dtype=np.uint8)

            #print(yolo_results)
            
            # 处理检测结果
            detected = False
            for result in yolo_results:
                boxes = result.boxes
                for box in boxes:
                    print(box)
                    confidence = float(box.conf)
                    if confidence < 0.7:
                        continue

                    detected = True
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    bbox = [int(x1), int(y1), int(x2), int(y2)]
                    
                    # 使用SAM进行初始分割
                    sam_results = self.sam_model(color_image, bboxes=[bbox])
                    
                    if sam_results and len(sam_results) > 0:
                        sam_mask = sam_results[0].masks.data[0].cpu().numpy()
                        sam_mask = (sam_mask * 255).astype(np.uint8)
                        
                        # 使用GMM改进分割结果
                        improved_mask = self.process_mask_with_gmm(color_image, sam_mask)
                        
                        # 更新掩码
                        mask = cv2.bitwise_or(mask, improved_mask)
                        
                        # 保存改进后的掩码用于调试
                        cv2.imwrite('/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/pictures/improved_mask.jpg', 
                                  improved_mask)
                    
                    # 在原图上显示检测框和轮廓
                    cv2.rectangle(color_image, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)

            if not detected:
                cv2.imwrite('/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/pictures/failed_detection.jpg', 
                           cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR))
                raise Exception("未检测到目标")

            # 保存结果图像
            cv2.imwrite('/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/pictures/mask_result.jpg', mask)

            # 获取机械臂当前状态
            ret, state_dict = robot.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取机械臂状态失败，错误码：{ret}")
            
            pose = state_dict['pose']
            print("/n==== 当前状态 ====")
            print("当前位姿:", pose)

            # 1. 第一次计算目标位姿
            above_object_pose, correct_angle_pose, finally_pose  = vertical_catch(
                mask, 
                depth_image, 
                color_intr, 
                pose,  
                100,   
                self.gripper_offset,
                self.rotation_matrix, 
                self.translation_vector
            )

            # 2. 移动到预备位置（让相机对准物体上方）
            print("移动到预备位置...")
            camera_above_pose = above_object_pose.copy()
            camera_above_pose[0] -= 0.08
            # camera_above_pose[1] -= 0.05
            
            print("/n==== 移动详细信息 ====")
            print(f"目标位姿: {camera_above_pose}")
            print(f"位置变化: dx={camera_above_pose[0]-pose[0]:.3f}, "
                  f"dy={camera_above_pose[1]-pose[1]:.3f}, "
                  f"dz={camera_above_pose[2]-pose[2]:.3f}")

            ret = robot.rm_movej_p(camera_above_pose, v=15, r=0, connect=0, block=1)
            if ret != 0:
                raise Exception(f"移动到预备位置失败，错误码：{ret}")
            time.sleep(1)

             # 3. 在相机正对物体时进行二次检测
            print("/n==== 二次检测 ====")
            color_image, depth_image, color_intr = self.get_frames_from_gui()
            if color_image is None or depth_image is None or color_intr is None:
                raise Exception("二次检测：无法获取图像或相机内参")
            
            # 二次YOLO检测和SAM分割
            yolo_results = self.yolo_model(color_image, verbose=False)
            mask = np.zeros((height, width), dtype=np.uint8)
            
            detected = False
            for result in yolo_results:
                boxes = result.boxes
                for box in boxes:
                    confidence = float(box.conf)
                    if confidence < 0.7:
                        continue
                    detected = True
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    bbox = [int(x1), int(y1), int(x2), int(y2)]
                    
                    sam_results = self.sam_model(color_image, bboxes=[bbox])
                    if sam_results and len(sam_results) > 0:
                        sam_mask = sam_results[0].masks.data[0].cpu().numpy()
                        sam_mask = (sam_mask * 255).astype(np.uint8)
                        
                        # 使用GMM改进二次检测的分割结果
                        improved_mask = self.process_mask_with_gmm(color_image, sam_mask)
                        
                        # 更新掩码
                        mask = cv2.bitwise_or(mask, improved_mask)
                        
                        # 保存二次检测改进后的掩码用于调试
                        cv2.imwrite('/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/pictures/second_improved_mask.jpg', improved_mask)

            if not detected:
                raise Exception("二次检测：未检测到目标")

            # 获取当前机械臂位姿
            ret, current_state = robot.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取当前状态失败，错误码：{ret}")
            current_pose = current_state['pose']

            # 4. 基于二次检测结果计算新的位姿和下降距离
            _, adjusted_angle_pose, adjusted_final_pose = vertical_catch(
                mask, 
                depth_image,  
                color_intr, 
                current_pose,
                100,
                self.gripper_offset,
                self.rotation_matrix, 
                self.translation_vector
            )

            # 5. 执行抓取动作序列
            print("/n==== 开始移动 ====")
            
            adjusted_final_pose[3] = self.gripper_offset[0]
            adjusted_final_pose[4] = self.gripper_offset[1]
            adjusted_final_pose[5] = self.gripper_offset[2]

            # 先移动到目标物体正上方（保持当前Z高度）
            print("移动到目标物体正上方...")
            above_target_pose = adjusted_final_pose.copy()
            above_target_pose[2] = current_pose[2]  # 保持当前Z高度
            above_target_pose[1] -= 0.015#0.015  # 稍微向左移动一点

            print("/n==== XY平面移动详细信息 ====")
            print(f"当前位姿: {current_pose}")
            print(f"目标上方位姿: {above_target_pose}")
            ret = robot.rm_movel(above_target_pose, v=15, r=0, connect=0, block=1)
            if ret != 0:
                raise Exception(f"移动到目标物体上方失败，错误码：{ret}")
            
            # 垂直下降到抓取位置
            print("垂直下降到抓取位置...")
            print("/n==== Z轴下降详细信息 ====")
            print(f"目标位姿: {adjusted_final_pose}")
            adjusted_final_pose[1] -= 0.015
            adjusted_final_pose[2] = -0.24
            ret = robot.rm_movel(adjusted_final_pose, v=15, r=0, connect=0, block=1)  # 降低速度进行精确抓取
            if ret != 0:
                raise Exception(f"垂直下降失败，错误码：{ret}")

            # 定义最大尝试次数
            max_attempts = 5
            attempts = 0

            while attempts < max_attempts:
                print("夹取物体...")
                ret = robot.rm_set_gripper_pick_on(
                    speed=100,    # 夹取速度
                    block=True,   # 阻塞模式
                    timeout=3,    # 超时时间3秒
                    force=300     # 力度100
                )

                if ret == 0:
                    print("夹取成功")
                    break
                else:
                    print(f"夹取失败，错误码：{ret}")
                    attempts += 1
                    time.sleep(1)  

            if attempts == max_attempts:
                print("达到最大尝试次数，夹取操作仍未成功")

            # 回到初始位姿
            print("回到初始位姿...")
            ret = robot.rm_movej_p(initial_pose, v=15, r=0, connect=0, block=1)
            if ret != 0:
                raise Exception(f"回到初始位姿失败，错误码：{ret}")

            if ret != 0:
                raise Exception(f"释放物体失败，错误码：{ret}")

            # 获取最终状态
            ret, final_state = robot.rm_get_current_arm_state()
            if ret == 0:
                print("/n==== 移动完成 ====")
                print("最终位姿:", final_state['pose'])
            
            return True

        except Exception as e:
            print(f"错误: {str(e)}")
            return False

    def init_robot1(self):
        """初始化第一个机械臂"""
        try:
            print("\n==== 初始化第一个机械臂 ====")
            if not self.robot1_ctrl.is_connected:
                self.robot1_ctrl.connect()

            robot1 = self.robot1_ctrl.robot
            ret, state = robot1.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取robot1状态失败，错误码：{ret}")
            if state.get('error_code', 0) != 0:
                raise Exception(f"robot1存在错误，错误码：{state['error_code']}")
            print(f"robot1当前状态: {state}")
            
            return robot1
            
        except Exception as e:
            print(f"robot1初始化过程出错: {str(e)}")
            return None

    def init_robot2(self):
        """初始化第二个机械臂"""
        try:
            print("\n==== 初始化第二个机械臂 ====")
            if not self.robot2_ctrl.is_connected:
                self.robot2_ctrl.connect()

            robot2 = self.robot2_ctrl.robot
            ret, state = robot2.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取robot2状态失败，错误码：{ret}")
            if state.get('error_code', 0) != 0:
                raise Exception(f"robot2存在错误，错误码：{state['error_code']}")
            print(f"robot2当前状态: {state}")
            
            return robot2
            
        except Exception as e:
            print(f"robot2初始化过程出错: {str(e)}")
            return None

    def spawn_robot1(self, robot1):
        """移动 robot1 到初始位置"""
        try:
            print("\n==== 移动 robot1 到初始位置 ====")
            target_pose1 = ROBOT1_CONFIG["initial_pose"]
            print(f"robot1 目标位置：{target_pose1}")
            ret = robot1.rm_movej_p(target_pose1, 
                                   v=MOVE_CONFIG["velocity"], 
                                   r=MOVE_CONFIG["radius"], 
                                   connect=MOVE_CONFIG["connect"], 
                                   block=MOVE_CONFIG["block"])
            if ret != 0:
                raise Exception(f"robot1移动失败，错误码：{ret}")
                
            ret, state = robot1.rm_get_current_arm_state()
            if ret == 0:
                print(f"robot1最终位置: {state['pose']}")
                
            print("robot1已就位！")
            return True
            
        except Exception as e:
            print(f"robot1移动过程出错: {str(e)}")
            return False

    def spawn_robot2(self, robot2):
        """移动 robot2 到初始位置"""
        try:
            print("\n==== 移动 robot2 到初始位置 ====")
            target_pose2 = ROBOT2_CONFIG["initial_pose"]
            print(f"robot2 目标位置：{target_pose2}")
            ret = robot2.rm_movej_p(target_pose2, 
                                   v=MOVE_CONFIG["velocity"], 
                                   r=MOVE_CONFIG["radius"], 
                                   connect=MOVE_CONFIG["connect"], 
                                   block=MOVE_CONFIG["block"])
            if ret != 0:
                raise Exception(f"robot2移动失败，错误码：{ret}")
                
            ret, state = robot2.rm_get_current_arm_state()
            if ret == 0:
                print(f"robot2最终位置: {state['pose']}")
                
            print("robot2已就位！")
            return True
            
        except Exception as e:
            print(f"robot2移动过程出错: {str(e)}")
            return False

    def openclaw(self, robot):
        """打开夹爪"""
        attempts = 0
        max_attempts = 5
        while attempts < max_attempts:
            print("打开夹爪...")
            ret = robot.rm_set_gripper_release(
                        speed=100,
                        block=True,
                        timeout=3)
            if ret == 0:
                print("释放成功")
                break
            else:
                print(f"释放失败，错误码：{ret}")
                attempts += 1
                time.sleep(1)

            if attempts == max_attempts:
                    print("达到最大尝试次数，释放操作仍未成功")
                    return False
        return True

    def closeclaw(self, robot):
        """关闭夹爪"""
        max_attempts = 5
        attempts = 0
        while attempts < max_attempts:
            print("夹取物体...")
            ret = robot.rm_set_gripper_pick_on(
                    speed=800,    # 夹取速度
                    block=True,   # 阻塞模式
                    timeout=3,    # 超时时间3秒
                    force=300     # 力度100
                )
            if ret == 0:
                print("夹取成功")
                time.sleep(1)
                break
            else:
                print(f"夹取失败，错误码：{ret}")
                attempts += 1
                time.sleep(1)
            if attempts == max_attempts:
                print("达到最大尝试次数，夹取操作仍未成功")
                return False
        return True

    def gripper_open_robot1(self):
        """直接打开Robot1夹爪（使用已连接的实例）"""
        if self.robot1_ctrl is None or self.robot1_ctrl.robot is None:
            raise Exception("Robot1未连接")
        return self.openclaw(self.robot1_ctrl.robot)

    def gripper_close_robot1(self):
        """直接关闭Robot1夹爪（使用已连接的实例）"""
        if self.robot1_ctrl is None or self.robot1_ctrl.robot is None:
            raise Exception("Robot1未连接")
        return self.closeclaw(self.robot1_ctrl.robot)

    def move_robot1(self, target_pose):
        """移动Robot1到指定点位（使用已连接的实例）"""
        if self.robot1_ctrl is None or self.robot1_ctrl.robot is None:
            raise Exception("Robot1未连接")
        try:
            print(f"移动Robot1到: {target_pose}")
            ret = self.robot1_ctrl.robot.rm_movej_p(
                target_pose,
                v=MOVE_CONFIG["velocity"],
                r=MOVE_CONFIG["radius"],
                connect=MOVE_CONFIG["connect"],
                block=MOVE_CONFIG["block"]
            )
            if ret == 0:
                print("Robot1移动成功")
                return True
            else:
                print(f"Robot1移动失败，错误码：{ret}")
                return False
        except Exception as e:
            print(f"Robot1移动出错: {str(e)}")
            return False

    def move_robot2(self, target_pose):
        """移动Robot2到指定点位（使用已连接的实例）"""
        if self.robot2_ctrl is None or self.robot2_ctrl.robot is None:
            raise Exception("Robot2未连接")
        try:
            print(f"移动Robot2到: {target_pose}")
            ret = self.robot2_ctrl.robot.rm_movej_p(
                target_pose,
                v=MOVE_CONFIG["velocity"],
                r=MOVE_CONFIG["radius"],
                connect=MOVE_CONFIG["connect"],
                block=MOVE_CONFIG["block"]
            )
            if ret == 0:
                print("Robot2移动成功")
                return True
            else:
                print(f"Robot2移动失败，错误码：{ret}")
                return False
        except Exception as e:
            print(f"Robot2移动出错: {str(e)}")
            return False

    def move_robot1l(self, target_pose):
        """移动Robot1到指定点位（使用已连接的实例）"""
        if self.robot1_ctrl is None or self.robot1_ctrl.robot is None:
            raise Exception("Robot1未连接")
        try:
            print(f"移动Robot1到: {target_pose}")
            ret = self.robot1_ctrl.robot.rm_movel(
                target_pose,
                v=MOVE_CONFIG["velocity"],
                r=MOVE_CONFIG["radius"],
                connect=MOVE_CONFIG["connect"],
                block=MOVE_CONFIG["block"]
            )
            if ret == 0:
                print("Robot1移动成功")
                return True
            else:
                print(f"Robot1移动失败，错误码：{ret}")
                return False
        except Exception as e:
            print(f"Robot1移动出错: {str(e)}")
            return False

    def move_robot2l(self, target_pose):
        """移动Robot2到指定点位（使用已连接的实例）"""
        if self.robot2_ctrl is None or self.robot2_ctrl.robot is None:
            raise Exception("Robot2未连接")
        try:
            print(f"移动Robot2到: {target_pose}")
            ret = self.robot2_ctrl.robot.rm_movel(
                target_pose,
                v=MOVE_CONFIG["velocity"],
                r=MOVE_CONFIG["radius"],
                connect=MOVE_CONFIG["connect"],
                block=MOVE_CONFIG["block"]
            )
            if ret == 0:
                print("Robot2移动成功")
                return True
            else:
                print(f"Robot2移动失败，错误码：{ret}")
                return False
        except Exception as e:
            print(f"Robot2移动出错: {str(e)}")
            return False
    def pick_gun1(self):
        """取枪头1（使用Robot2已连接的实例）"""
        if self.robot2_ctrl is None or self.robot2_ctrl.robot is None:
            raise Exception("Robot2未连接")
        robot = self.robot2_ctrl.robot
        pos_1shang = GUN1_POSITIONS["1shang"]
        pos_1xia = GUN1_POSITIONS["1xia"]

        print("取枪头1动作")
        ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"移动到1shang失败，错误码: {ret}")
            return False

        ret = robot.rm_movel(pos_1xia, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到1xia失败，错误码: {ret}")
            return False

        time.sleep(0.5)

        ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到1shang失败，错误码: {ret}")
            return False

        print("取枪头1完成!")
        return True

    def pick_gun2(self):
        """取枪头2（使用Robot2已连接的实例）"""
        if self.robot2_ctrl is None or self.robot2_ctrl.robot is None:
            raise Exception("Robot2未连接")
        robot = self.robot2_ctrl.robot
        pos_2shang = GUN2_POSITIONS["2shang"]
        pos_2xia = GUN2_POSITIONS["2xia"]

        print("取枪头2动作")
        ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"移动到2shang失败，错误码: {ret}")
            return False

        ret = robot.rm_movel(pos_2xia, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到2xia失败，错误码: {ret}")
            return False

        time.sleep(0.5)

        ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到2shang失败，错误码: {ret}")
            return False

        print("取枪头2完成!")
        return True

    def drop_gun1(self):
        """退枪头1（使用Robot2已连接的实例）"""
        if self.robot2_ctrl is None or self.robot2_ctrl.robot is None:
            raise Exception("Robot2未连接")
        robot = self.robot2_ctrl.robot
        pos_1shang = GUN1_POSITIONS["1shang"]
        pos_1zhong = GUN1_POSITIONS["1zhong"]

        print("退枪头1动作")
        ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"移动到1shang失败，错误码: {ret}")
            return False

        ret = robot.rm_movel(pos_1zhong, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到1zhong失败，错误码: {ret}")
            return False

        print(f"\n[4] 退枪头（弹出枪头）...")
        result = yiyeqiang_out.eject_tip(port='/dev/hand')
        if result:
            print("退枪头成功!")
        else:
            print("退枪头失败!")
            return False

        time.sleep(1)

        ret = robot.rm_movel(pos_1shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到1shang失败，错误码: {ret}")
            return False

        print("退枪头1完成!")
        return True

    def drop_gun2(self):
        """退枪头2（使用Robot2已连接的实例）"""
        if self.robot2_ctrl is None or self.robot2_ctrl.robot is None:
            raise Exception("Robot2未连接")
        robot = self.robot2_ctrl.robot
        pos_2shang = GUN2_POSITIONS["2shang"]
        pos_2zhong = GUN2_POSITIONS["2zhong"]

        print("退枪头2动作")
        ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"移动到2shang失败，错误码: {ret}")
            return False

        ret = robot.rm_movel(pos_2zhong, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到2zhong失败，错误码: {ret}")
            return False
        
        print(f"\n[4] 退枪头（弹出枪头）...")
        result = yiyeqiang_out.eject_tip(port='/dev/hand')
        if result:
            print("退枪头成功!")
        else:
            print("退枪头失败!")
            return False

        time.sleep(0.5)

        ret = robot.rm_movel(pos_2shang, MOVE_CONFIG["velocity"], MOVE_CONFIG["radius"], 0, 1)
        if ret != 0:
            print(f"movel到2shang失败，错误码: {ret}")
            return False

        print("退枪头2完成!")
        return True

    def move_to_plate(self, robot):
        """移动到放置位置"""
        try:
            print("\n==== 移动robot1到放置位置 ====")
            target_pose1 = [65.02 / 1000, 171.949 / 1000, -178.843 / 1000, -3.104, -0.045, -3]
            print(f"robot1目标位置: {target_pose1}")
            ret = robot.rm_movej_p(target_pose1, 
                                   v=MOVE_CONFIG["velocity"], 
                                   r=MOVE_CONFIG["radius"], 
                                   connect=MOVE_CONFIG["connect"], 
                                   block=MOVE_CONFIG["block"])
            if ret != 0:
                raise Exception(f"robot1移动失败，错误码：{ret}")
            
            target_pose2 = target_pose1.copy()
            target_pose2[2] = -301.896 / 1000

            ret = robot.rm_movel(target_pose2, 
                                   v=MOVE_CONFIG["velocity"], 
                                   r=MOVE_CONFIG["radius"], 
                                   connect=MOVE_CONFIG["connect"], 
                                   block=MOVE_CONFIG["block"])
            if ret != 0:
                raise Exception(f"robot1移动失败，错误码：{ret}")
            
        except Exception as e:
            print(f"robot1移动过程出错: {str(e)}")
            return False

    def execute_robot_task_unlock(self, robot):
        """执行机械臂解锁任务"""
        try:
            ret = robot.rm_movej_p(self.TOOL_POINTS[0]["pose"], v=10, r=0, connect=0, block=1)
            ret = robot.rm_movel(self.TOOL_POINTS[1]["pose"], v=10, r=0, connect=0, block=1)
            ret = robot.rm_movel(self.TOOL_POINTS[2]["pose"], v=5, r=0, connect=0, block=1)

            self.relay_controller.turn_off_relay_Y2()
            time.sleep(2)
            
            max_attempts = 5
            attempt = 0
            while True:
                print(f"第 {attempt + 1} 次尝试上锁...")
                
                res = self.khs.send_command('close')
                time.sleep(0.5)
                
                status = self.khs.send_command('status')
                print(f"当前状态: {status}")
                
                if status == "locked":
                    print("上锁成功！")
                    break
                
                attempt += 1
                if attempt >= max_attempts:
                    print(f"警告：尝试{max_attempts}次后仍未成功上锁")
                    break
                    
                print("上锁未成功，等待后重试...")
                time.sleep(1)
            
            ret = robot.rm_movel(self.TOOL_POINTS[1]["pose"], v=10, r=0, connect=0, block=1)
            ret = robot.rm_movel(self.TOOL_POINTS[0]["pose"], v=10, r=0, connect=0, block=1)

            print("任务执行完成")
            return True
        except Exception as e:
            print(f"执行任务出错: {str(e)}")
            return False
        finally:
            self.relay_controller.close()
            self.khs.close()

    def execute_robot_task_lock(self, robot):
        """执行机械臂锁定任务"""
        try:
            ret = robot.rm_movel(self.TOOL_POINTS[1]["pose"], v=10, r=0, connect=0, block=1)
            ret = robot.rm_movel(self.TOOL_POINTS[2]["pose"], v=10, r=0, connect=0, block=1)

            print("执行锁定操作")
            res = self.khs.send_command('open')
            time.sleep(0.5)
            status = self.khs.send_command('status')
            if status == "unlocked":
                res = self.khs.send_command('open')

            self.relay_controller.turn_on_relay_Y2()
            time.sleep(2)

            ret = robot.rm_movel(self.TOOL_POINTS[1]["pose"], v=10, r=0, connect=0, block=1)
            ret = robot.rm_movel(self.TOOL_POINTS[0]["pose"], v=10, r=0, connect=0, block=1)

            print("快换手锁定任务执行完成")
            
        finally:
            self.relay_controller.close()
            self.khs.close()

    def execute_trajectory(self, robot, file_path):
        """执行轨迹文件"""
        if not self.demo_send_project(robot, file_path):
            return False
            
        time.sleep(1)
        while True:
            rst = self.demo_get_program_run_state(robot, time_sleep=1, max_retries=1)
            time.sleep(0.5)
            print(rst)
            if rst:
                return True
        return False

    def demo_send_project(self, robot, file_path, plan_speed=20, only_save=0, save_id=0, step_flag=0, auto_start=0, project_type=1):
        """向机械臂发送项目"""
        if not os.path.exists(file_path):
            print("文件路径不存在:", file_path)
            return False

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
            return False

    def demo_get_program_run_state(self, robot, time_sleep=1, max_retries=10):
        """获取程序运行状态"""
        retries = 0
        while retries < max_retries:
            time.sleep(time_sleep)
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

if __name__ == "__main__":
    # 创建RobotController实例
    controller = RobotController()
    try:
        # 初始化robot2
        robot2 = controller.init_robot2()
        if robot2 is not None:
            success2 = controller.spawn_robot2(robot2)
        else:
            success2 = False
        
        time.sleep(1)
        
        # 初始化robot1
        robot1 = controller.init_robot1()
        if robot1 is not None:
            success1 = controller.spawn_robot1(robot1)
        else:
            success1 = False
        
        if success1 and success2:
            print("\n两个机械臂都已成功移动到初始位置！")
        else:
            print("\n机械臂移动过程中出现错误，请检查日志。")
            
        # 执行轨迹
        print("\n开始执行轨迹...")
        robot2 = controller.init_robot2()
        success = controller.execute_trajectory(robot2, "/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/code/Path/trajectory_1.txt")
        if success:
            print("轨迹执行成功")
            print("\n开始执行快换手任务...")
            success = controller.execute_robot_task_unlock(robot2)
            if success:
                print("快换手任务执行成功！")
            else:
                print("快换手任务执行失败！")
        else:
            print("轨迹执行失败")

        # 初始化移液枪
        adp = ADP(port='COM4')
        adp.initialize()
        time.sleep(1)

        success = controller.execute_trajectory(robot2, "/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/code/Path/trajectory_2.txt")
        if success:
            print("轨迹执行成功")
            ret, current_state = robot2.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取当前状态失败，错误码：{ret}")
            
            current_pos = current_state['pose']
            current_pos[4] = 0
            robot2.rm_movel(current_pos, v=10, r=0, connect=0, block=1)

            print("\n4. 下降到取液高度...")
            current_pos[2] -= 0.12
            robot2.rm_movel(current_pos, v=10, r=0, connect=0, block=1)

            print("\n5. 开始取液...")
            adp.absorb(800)

            print("\n6. 上升")
            current_pos[2] += 0.12
            robot2.rm_movel(current_pos, v=10, r=0, connect=0, block=1)

            success = controller.execute_trajectory(robot2, "/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/code/Path/trajectory_3.txt")
            if success:
                print("轨迹执行成功")
                adp.dispense(800)
                time.sleep(1)
            else:
                print("轨迹执行失败")

            success = controller.execute_trajectory(robot2, "/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/code/Path/trajectory_4.txt")
            if success:
                success = controller.execute_robot_task_lock(robot2)
            else:
                print("轨迹执行失败")   
        else:
            print("轨迹执行失败")
    finally:
        controller.shutdown()
