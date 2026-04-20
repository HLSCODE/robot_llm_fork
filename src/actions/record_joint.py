# -*- coding: utf-8 -*-
"""
机械臂关节角记录工具 - GUI版本
点击按钮即可记录当前机械臂关节角
"""
import tkinter as tk
from tkinter import messagebox, simpledialog
import os
import sys
import time
from datetime import datetime
from Robotic_Arm.rm_robot_interface import *
from ..arm_sdk.config import ROBOT1_CONFIG, ROBOT2_CONFIG


class JointRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("机械臂关节角记录工具")
        self.root.geometry("500x600")
        self.root.resizable(False, False)
        
        # 机械臂连接状态
        self.robot1 = None
        self.robot2 = None
        self.robot1_connected = False
        self.robot2_connected = False
        
        # 记录文件路径
        self.record_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "joint_records.txt")
        
        # 初始化UI
        self.init_ui()
        
        # 启动时自动连接机械臂
        self.connect_robots()
    
    def init_ui(self):
        """初始化UI组件"""
        # 标题
        title_label = tk.Label(self.root, text="机械臂关节角记录工具", 
                              font=("微软雅黑", 18, "bold"))
        title_label.pack(pady=15)
        
        # 连接状态区域
        status_frame = tk.LabelFrame(self.root, text="连接状态", font=("微软雅黑", 12))
        status_frame.pack(pady=10, padx=20, fill="x")
        
        # Robot1状态
        self.robot1_status_label = tk.Label(status_frame, text="Robot1: 未连接",
                                           font=("微软雅黑", 10), fg="gray")
        self.robot1_status_label.pack(pady=5)
        
        # Robot2状态
        self.robot2_status_label = tk.Label(status_frame, text="Robot2: 未连接",
                                           font=("微软雅黑", 10), fg="gray")
        self.robot2_status_label.pack(pady=5)
        
        # 按钮区域
        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=20)

        # Robot1记录按钮
        btn_robot1_frame = tk.Frame(button_frame)
        btn_robot1_frame.pack(pady=5)
        
        self.btn_robot1_joint = tk.Button(btn_robot1_frame, text="记录 Robot1 关节角",
                                    font=("微软雅黑", 11), width=18, height=1,
                                    bg="#4CAF50", fg="white",
                                    command=self.record_robot1_joint,
                                    state="disabled")
        self.btn_robot1_joint.pack(side="left", padx=5)
        
        self.btn_robot1_pose = tk.Button(btn_robot1_frame, text="记录 Robot1 位姿",
                                    font=("微软雅黑", 11), width=18, height=1,
                                    bg="#8BC34A", fg="white",
                                    command=self.record_robot1_pose,
                                    state="disabled")
        self.btn_robot1_pose.pack(side="left", padx=5)
        
        # Robot2记录按钮
        btn_robot2_frame = tk.Frame(button_frame)
        btn_robot2_frame.pack(pady=5)
        
        self.btn_robot2_joint = tk.Button(btn_robot2_frame, text="记录 Robot2 关节角",
                                    font=("微软雅黑", 11), width=18, height=1,
                                    bg="#2196F3", fg="white",
                                    command=self.record_robot2_joint,
                                    state="disabled")
        self.btn_robot2_joint.pack(side="left", padx=5)
        
        self.btn_robot2_pose = tk.Button(btn_robot2_frame, text="记录 Robot2 位姿",
                                    font=("微软雅黑", 11), width=18, height=1,
                                    bg="#03A9F4", fg="white",
                                    command=self.record_robot2_pose,
                                    state="disabled")
        self.btn_robot2_pose.pack(side="left", padx=5)
        
        # 显示区域
        display_frame = tk.LabelFrame(self.root, text="当前记录", font=("微软雅黑", 12))
        display_frame.pack(pady=10, padx=20, fill="both", expand=True)
        
        # Robot1关节角显示
        self.robot1_joints_label = tk.Label(display_frame, text="Robot1 关节角: --",
                                            font=("Consolas", 10))
        self.robot1_joints_label.pack(pady=2, anchor="w", padx=10)

        # Robot1位姿显示
        self.robot1_pose_label = tk.Label(display_frame, text="Robot1 位姿: --",
                                           font=("Consolas", 10))
        self.robot1_pose_label.pack(pady=2, anchor="w", padx=10)
        
        # Robot2关节角显示
        self.robot2_joints_label = tk.Label(display_frame, text="Robot2 关节角: --",
                                            font=("Consolas", 10))
        self.robot2_joints_label.pack(pady=2, anchor="w", padx=10)

        # Robot2位姿显示
        self.robot2_pose_label = tk.Label(display_frame, text="Robot2 位姿: --",
                                          font=("Consolas", 10))
        self.robot2_pose_label.pack(pady=2, anchor="w", padx=10)
        
        # 记录次数
        self.record_count_label = tk.Label(display_frame, text="已记录次数: 0",
                                           font=("微软雅黑", 10), fg="#666")
        self.record_count_label.pack(pady=10)
        
        # 文件路径显示
        file_label = tk.Label(self.root, text=f"保存文件: {self.record_file}",
                             font=("微软雅黑", 9), fg="#888")
        file_label.pack(side="bottom", pady=10)
        
        # 记录计数
        self.record_count = 0
    
    def connect_robots(self):
        """连接机械臂"""
        try:
            # 连接Robot1
            try:
                print("正在连接Robot1...")
                self.robot1 = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
                self.robot1.rm_create_robot_arm(ROBOT1_CONFIG["ip"], ROBOT1_CONFIG["port"])
                ret, state = self.robot1.rm_get_current_arm_state()
                if ret == 0:
                    self.robot1_connected = True
                    self.robot1_status_label.config(text="Robot1: 已连接", fg="green")
                    self.btn_robot1_joint.config(state="normal")
                    self.btn_robot1_pose.config(state="normal")
                    print("Robot1连接成功")
            except Exception as e:
                print(f"Robot1连接失败: {e}")
                self.robot1_status_label.config(text=f"Robot1: 连接失败", fg="red")
            
            # 连接Robot2
            try:
                print("正在连接Robot2...")
                self.robot2 = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
                self.robot2.rm_create_robot_arm(ROBOT2_CONFIG["ip"], ROBOT2_CONFIG["port"])
                ret, state = self.robot2.rm_get_current_arm_state()
                if ret == 0:
                    self.robot2_connected = True
                    self.robot2_status_label.config(text="Robot2: 已连接", fg="green")
                    self.btn_robot2_joint.config(state="normal")
                    self.btn_robot2_pose.config(state="normal")
                    print("Robot2连接成功")
            except Exception as e:
                print(f"Robot2连接失败: {e}")
                self.robot2_status_label.config(text=f"Robot2: 连接失败", fg="red")
                
        except Exception as e:
            print(f"连接错误: {e}")
    
    def get_joint_degrees(self, robot, robot_name):
        """获取机械臂关节角"""
        try:
            ret, joint_degree = robot.rm_get_joint_degree()
            if ret != 0:
                raise Exception(f"获取{robot_name}关节角失败，错误码：{ret}")
            return joint_degree
        except Exception as e:
            print(f"获取{robot_name}关节角出错: {e}")
            return None

    def get_current_pose(self, robot, robot_name):
        """获取机械臂末端位姿 (x, y, z, rx, ry, rz)"""
        try:
            ret, state = robot.rm_get_current_arm_state()
            if ret != 0:
                raise Exception(f"获取{robot_name}位姿失败，错误码：{ret}")
            pose = state['pose']  # [x, y, z, rx, ry, rz]，单位：米
            return pose
        except Exception as e:
            print(f"获取{robot_name}位姿出错: {e}")
            return None
    
    def save_joint_to_file(self, joint_data, robot_name):
        """保存关节角到文件"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            with open(self.record_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*50}\n")
                f.write(f"记录时间: {current_time}\n")
                f.write(f"机械臂: {robot_name}\n")
                f.write(f"{'='*50}\n")
                for i, j in enumerate(joint_data):
                    f.write(f"关节{i+1} (J{i+1}): {j:.3f}°\n")
                # 原始数据格式
                f.write(f"\n[RAW DATA - JOINT]\n")
                f.write(f"{','.join([f'{j:.3f}' for j in joint_data])}\n")
            
            return True
        except Exception as e:
            print(f"保存失败: {e}")
            return False

    def save_pose_to_file(self, pose_data, robot_name, label="未命名"):
        """保存位姿到文件"""
        try:
            # 生成变量名，如 1shang -> pos_1shang
            var_name = f"pos_{label}"

            with open(self.record_file, 'a', encoding='utf-8') as f:
                # 直接写入变量赋值格式
                f.write(f"\n{var_name} = [{','.join([f'{p:.6f}' for p in pose_data])}]\n")

            return True
        except Exception as e:
            print(f"保存失败: {e}")
            return False
    
    def record_robot1_joint(self):
        """记录Robot1关节角"""
        if not self.robot1_connected:
            messagebox.showwarning("警告", "Robot1未连接")
            return

        joint_data = self.get_joint_degrees(self.robot1, "Robot1")
        if joint_data:
            # 显示关节角
            joint_str = ", ".join([f"J{i+1}:{j:.1f}°" for i, j in enumerate(joint_data)])
            self.robot1_joints_label.config(text=f"Robot1 关节角: {joint_str}")

            # 保存到文件
            if self.save_joint_to_file(joint_data, "Robot1"):
                self.record_count += 1
                self.record_count_label.config(text=f"已记录次数: {self.record_count}")
                messagebox.showinfo("成功", f"Robot1关节角已记录!\n{joint_str}")

    def record_robot1_pose(self):
        """记录Robot1位姿"""
        if not self.robot1_connected:
            messagebox.showwarning("警告", "Robot1未连接")
            return

        # 弹出输入框让用户输入标签
        label = tk.simpledialog.askstring("输入标签", "请输入位姿标签（如 1shang, 1xia, 2shang 等）:")
        if not label:
            return

        pose_data = self.get_current_pose(self.robot1, "Robot1")
        if pose_data:
            # 显示位姿
            pose_str = f"X:{pose_data[0]*1000:.1f} Y:{pose_data[1]*1000:.1f} Z:{pose_data[2]*1000:.1f}"
            self.robot1_pose_label.config(text=f"Robot1 位姿: {pose_str}")

            # 保存到文件
            if self.save_pose_to_file(pose_data, "Robot1", label):
                self.record_count += 1
                self.record_count_label.config(text=f"已记录次数: {self.record_count}")
                messagebox.showinfo("成功", f"Robot1位姿已记录!\n标签: {label}\n{pose_str}")

    def record_robot2_joint(self):
        """记录Robot2关节角"""
        if not self.robot2_connected:
            messagebox.showwarning("警告", "Robot2未连接")
            return

        joint_data = self.get_joint_degrees(self.robot2, "Robot2")
        if joint_data:
            # 显示关节角
            joint_str = ", ".join([f"J{i+1}:{j:.1f}°" for i, j in enumerate(joint_data)])
            self.robot2_joints_label.config(text=f"Robot2 关节角: {joint_str}")

            # 保存到文件
            if self.save_joint_to_file(joint_data, "Robot2"):
                self.record_count += 1
                self.record_count_label.config(text=f"已记录次数: {self.record_count}")
                messagebox.showinfo("成功", f"Robot2关节角已记录!\n{joint_str}")

    def record_robot2_pose(self):
        """记录Robot2位姿"""
        if not self.robot2_connected:
            messagebox.showwarning("警告", "Robot2未连接")
            return

        # 弹出输入框让用户输入标签
        label = tk.simpledialog.askstring("输入标签", "请输入位姿标签（如 1shang, 1xia, 2shang 等）:")
        if not label:
            return

        pose_data = self.get_current_pose(self.robot2, "Robot2")
        if pose_data:
            # 显示位姿
            pose_str = f"X:{pose_data[0]*1000:.1f} Y:{pose_data[1]*1000:.1f} Z:{pose_data[2]*1000:.1f}"
            self.robot2_pose_label.config(text=f"Robot2 位姿: {pose_str}")

            # 保存到文件
            if self.save_pose_to_file(pose_data, "Robot2", label):
                self.record_count += 1
                self.record_count_label.config(text=f"已记录次数: {self.record_count}")
                messagebox.showinfo("成功", f"Robot2位姿已记录!\n标签: {label}\n{pose_str}")

    def on_closing(self):
        """关闭窗口时断开连接"""
        try:
            if self.robot1:
                self.robot1.rm_delete_robot_arm()
            if self.robot2:
                self.robot2.rm_delete_robot_arm()
        except:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = JointRecorderApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
