# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import messagebox  # edit: 引入弹窗支持
import subprocess
import os
import cv2
import pyrealsense2 as rs
import numpy as np
import socket
import threading
import pickle
import struct
import time
import serial  # edit: Modbus 驱动串口
from ..devices import yiyeqiang_out  # 移液枪弹出枪头模块
from ..devices import yiyeqiang_init  # 移液枪初始化模块
from PIL import Image, ImageTk
from ..arm_sdk.controller import RobotController
from .base_controller import RobotController as BSCTL
from ..devices.adp import ADP
from ultralytics import YOLO, SAM


# 初始化模型
yolo_model = YOLO("/home/maic/rm1/runs/train/bottle/weights/best.pt")
sam_model = SAM("/home/maic/rm1/RM_API2-main/RM_API2-main/Demo/vertical_grab/code/robot_arm/sam2.1_l.pt")

# 声明全局变量
baseCtl = BSCTL()


class ModbusMotor:
    """edit: 嵌入 gui.py 中的 Modbus 驱动封装"""

    def __init__(self, port="/dev/ttyUSB1", baudrate=115200, slave_id=1, timeout=1):
        self.slave_id = slave_id
        self.serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=timeout
        )
        if not self.serial.is_open:
            raise RuntimeError("串口打开失败")

        self.trigger = 0x6002
        self.pr0_mode = 0x6200
        self.pos_high = 0x6201
        self.pos_low = 0x6202
        self.speed = 0x6203
        self.acc = 0x6204
        self.dec = 0x6205
        self.enable_addr = 0x000F

        self.move_init()
        self.enable()

    def _calculate_crc(self, data):
        crc = 0xFFFF
        for pos in data:
            crc ^= pos
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if (crc & 1) else crc >> 1
        return crc

    def _create_modbus_frame(self, func, addr, value=None, cnt=1):
        if func == 0x03:
            frame = bytearray([self.slave_id, func, addr >> 8, addr & 0xFF, cnt >> 8, cnt & 0xFF])
        elif func == 0x06:
            frame = bytearray([self.slave_id, func, addr >> 8, addr & 0xFF, value >> 8, value & 0xFF])
        else:
            raise ValueError("不支持的功能码")
        crc = self._calculate_crc(frame)
        frame += bytearray([crc & 0xFF, crc >> 8])
        return frame

    def write_register(self, addr, val):
        frame = self._create_modbus_frame(0x06, addr, val)
        self.serial.reset_input_buffer()
        self.serial.write(frame)
        time.sleep(0.02)
        rsp = self.serial.read(8)
        if len(rsp) != 8 or rsp[0] != self.slave_id or rsp[1] != 0x06:
            raise RuntimeError("写寄存器异常")

    def read_holding_registers(self, addr, cnt=1):
        frame = self._create_modbus_frame(0x03, addr, cnt=cnt)
        self.serial.reset_input_buffer()
        self.serial.write(frame)
        time.sleep(0.02)
        rsp = self.serial.read(5 + 2 * cnt)
        if len(rsp) < 5 or rsp[0] != self.slave_id or rsp[1] != 0x03:
            raise RuntimeError("读寄存器异常")
        return [(rsp[3 + i * 2] << 8) | rsp[4 + i * 2] for i in range(cnt)]

    def enable(self):
        self.write_register(self.enable_addr, 0x0001)
        time.sleep(0.2)

    def emergency_stop(self):
        try:
            self.write_register(self.trigger, 0x0040)
            time.sleep(0.1)
            self.enable()
        except Exception:
            pass

    def move_init(self):
        self.write_register(self.pr0_mode, 0x0001)
        self.write_register(self.speed, 0x0058)
        self.write_register(self.acc, 0x0032)
        self.write_register(self.dec, 0x0032)
        time.sleep(0.2)

    def split_32bit(self, value):
        return (value >> 16) & 0xFFFF, value & 0xFFFF

    def move_to(self, pos):
        high, low = self.split_32bit(pos)
        self.write_register(self.pr0_mode, 0x0001)
        self.write_register(self.pos_high, high)
        self.write_register(self.pos_low, low)
        self.write_register(self.trigger, 0x0010)

    def is_reached(self):
        try:
            return self.read_holding_registers(self.trigger, 1)[0] == 0
        except Exception:
            return None

    def to_zero(self):
        self.write_register(0x600a, 0x000c)
        self.write_register(0x600f, 0x0064)
        self.write_register(self.trigger, 0x0020)
        while not self.is_reached():
            time.sleep(0.2)

    def close(self):
        if self.serial and self.serial.is_open:
            self.serial.close()


class MotorControlPanel:
    """edit: GUI 中的 Modbus 控制面板"""

    def __init__(self, parent, button_style):
        self.parent = parent
        self.motor = None
        self.frame = tk.Frame(parent, bg='#ecf0f1')
        self.frame.pack(fill='x')

        title = tk.Label(self.frame, text="Modbus 电机控制", font=("WenQuanYi Micro Hei", 12, "bold"),
                         bg='#ecf0f1', fg='#2c3e50')
        title.pack(anchor='w', pady=(0, 5))

        self.entry_pos = tk.Entry(self.frame, font=("WenQuanYi Micro Hei", 10))
        self.entry_pos.pack(fill='x', pady=5)

        self.enable_btn = tk.Button(self.frame, text="连接并使能", command=self.connect_motor, **button_style)
        self.enable_btn.pack(fill='x', pady=3)

        self.move_btn = tk.Button(self.frame, text="执行移动", command=self.move_motor,
                                  state=tk.DISABLED, **button_style)
        self.move_btn.pack(fill='x', pady=3)

        self.home_btn = tk.Button(self.frame, text="回零", command=self.move_home,
                                  state=tk.DISABLED, **button_style)
        self.home_btn.pack(fill='x', pady=3)

        pos_frame = tk.Frame(self.frame, bg='#ecf0f1')
        pos_frame.pack(fill='x', pady=5)

        fixed_positions = [
            ("100k", 100000),
            ("170k", 170000),
            ("200k", 200000),
            ("300k", 300000),
            ("318k", 318000)
        ]

        self.fixed_buttons = []
        for idx, (label, pos) in enumerate(fixed_positions):
            btn = tk.Button(
                pos_frame,
                text=label,
                width=8,
                state=tk.DISABLED,
                command=lambda p=pos: self.move_fixed(p)
            )
            btn.grid(row=idx // 3, column=idx % 3, padx=5, pady=3, sticky='ew')
            self.fixed_buttons.append(btn)

        self.estop_btn = tk.Button(
            self.frame,
            text="急停",
            bg='#e74c3c',
            fg='white',
            font=('WenQuanYi Micro Hei', 10, 'bold'),
            command=self.emergency_stop
        )
        self.estop_btn.pack(fill='x', pady=5)

        self.status_label = tk.Label(self.frame, text="状态: 未连接", fg='#e74c3c', bg='#ecf0f1',
                                     font=("WenQuanYi Micro Hei", 9))
        self.status_label.pack(fill='x', pady=(5, 0))

    def connect_motor(self):
        try:
            self.motor = ModbusMotor()
            self.update_status("已连接", '#27ae60')
            self._set_buttons_state(tk.NORMAL)
            threading.Thread(target=self._auto_home, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("连接失败", str(exc))

    def _auto_home(self):
        try:
            self.update_status("回零中...", '#f39c12')
            self.motor.to_zero()
            self.update_status("回零完成", '#27ae60')
        except Exception as exc:
            self.update_status(f"回零失败: {exc}", '#e74c3c')

    def move_motor(self):
        if not self.motor:
            messagebox.showwarning("提示", "请先连接电机")
            return
        try:
            pos = int(self.entry_pos.get())
        except ValueError:
            messagebox.showwarning("提示", "请输入有效位置")
            return
        self._start_motion(pos)

    def move_fixed(self, pos):
        if not self.motor:
            messagebox.showwarning("提示", "请先连接电机")
            return
        self._start_motion(pos)

    def move_home(self):
        if not self.motor:
            messagebox.showwarning("提示", "请先连接电机")
            return
        self._start_home()

    def _start_home(self):
        self.update_status("回零中...", '#f39c12')
        self._set_buttons_state(tk.DISABLED)
        threading.Thread(target=self._run_home, daemon=True).start()

    def _run_home(self):
        try:
            self.motor.to_zero()
            self.update_status("回零完成", '#27ae60')
        except Exception as exc:
            self.update_status(f"回零失败: {exc}", '#e74c3c')
        finally:
            self._set_buttons_state(tk.NORMAL)

    def _start_motion(self, pos):
        self.update_status("移动中...", '#f39c12')
        self._set_buttons_state(tk.DISABLED)
        threading.Thread(target=self._run_motion, args=(pos,), daemon=True).start()

    def _run_motion(self, pos):
        try:
            self.motor.move_to(pos)
            while True:
                state = self.motor.is_reached()
                if state is None:
                    self.update_status("通讯错误", '#e74c3c')
                    break
                if state:
                    self.update_status("到位", '#27ae60')
                    break
                time.sleep(0.3)
        except Exception as exc:
            self.update_status(f"移动失败: {exc}", '#e74c3c')
        finally:
            self._set_buttons_state(tk.NORMAL)

    def emergency_stop(self):
        if not self.motor:
            return
        try:
            self.motor.emergency_stop()
            self.update_status("急停已触发", '#e74c3c')
        except Exception as exc:
            self.update_status(f"急停失败: {exc}", '#e74c3c')

    def update_status(self, text, color):
        self.status_label.config(text=f"状态: {text}", fg=color)

    def _set_buttons_state(self, state):
        self.move_btn.config(state=state)
        self.home_btn.config(state=state)
        for btn in self.fixed_buttons:
            btn.config(state=state)

    def shutdown(self):
        if self.motor:
            self.motor.close()
            self.motor = None


class RobotRuntime:
    """封装机器人控制器与服务器的生命周期"""

    def __init__(self):
        self.controller = None
        self.robot1 = None
        self.robot2 = None
        self.initialized = False

    def initialize(self):
        """初始化机械臂"""
        if self.initialized:
            return True

        try:
            print("正在初始化双机械臂控制器...")
            self.controller = RobotController()
            # RobotController的__init__已经连接了机器人，这里直接获取引用
            self.robot1 = self.controller.robot1_ctrl.robot
            self.robot2 = self.controller.robot2_ctrl.robot

            self.initialized = True
            return True

        except Exception as exc:
            print(f"初始化机器人失败: {exc}")
            self.shutdown()
            return False

    def shutdown(self):
        """释放资源"""
        if self.controller is not None:
            try:
                self.controller.shutdown()
            except Exception as exc:
                print(f"关闭机械臂控制器失败: {exc}")
            finally:
                self.controller = None
                self.robot1 = None
                self.robot2 = None
                self.initialized = False

class CameraFrame(tk.Frame):

    def __init__(self, parent):
        super().__init__(parent, bg='#f8f9fa')
        self.parent = parent
        
        # 添加帧缓存和锁
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.current_depth_frame = None
        self.color_intr = None
        
        # 添加客户端连接状态标志
        self.client_connected = False
        self.client_lock = threading.Lock()
        
        # 创建标题
        self.title = tk.Label(self, text="实时预览", font=("WenQuanYi Micro Hei", 14, "bold"), 
                            bg='#f8f9fa', fg='#333')
        self.title.pack(pady=(5, 10))
        
        # 创建视频显示标签
        self.video_label = tk.Label(self)
        self.video_label.pack()
        
        # 初始化RealSense相机
        self.pipeline = rs.pipeline()
        self.config = rs.config()
        
        # 配置流
        self.config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        self.config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        
        try:
            # 启动相机流
            pipeline_profile = self.pipeline.start(self.config)
            
            # 获取相机内参
            color_stream = pipeline_profile.get_stream(rs.stream.color)
            color_intrinsics = color_stream.as_video_stream_profile().get_intrinsics()
            
            self.color_intr = {
                "ppx": color_intrinsics.ppx,
                "ppy": color_intrinsics.ppy,
                "fx": color_intrinsics.fx,
                "fy": color_intrinsics.fy
            }
            
            print("相机内参:", self.color_intr)
            
            # 等待稳定的图像
            time.sleep(1)
            
            self.is_running = True
            print("相机初始化成功")
            
            # 启动图像更新线程
            self.update_thread = threading.Thread(target=self.capture_frames, daemon=True)
            self.update_thread.start()
            
            # 启动GUI更新
            self.update_gui()
            
        except Exception as e:
            print(f"相机初始化失败: {e}")
            self.is_running = False
        
        # 启动socket服务器
        self.start_socket_server()
    
    def start_socket_server(self):
        """启动socket服务器"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('localhost', 12345))
        self.server_socket.listen(5)
        self.server_socket.setblocking(False)  # 设置为非阻塞模式
        
        # 在新线程中处理客户端连接
        self.server_thread = threading.Thread(target=self.handle_clients, daemon=True)
        self.server_thread.start()
    
    def handle_clients(self):
        """处理客户端连接的线程"""
        while self.is_running:
            try:
                client_socket, addr = self.server_socket.accept()
                print(f"客户端连接：{addr}")
                client_thread = threading.Thread(
                    target=self.handle_client_request,
                    args=(client_socket,),
                    daemon=True
                )
                client_thread.start()
            except BlockingIOError:
                # 非阻塞模式下，没有连接时会抛出此异常
                time.sleep(0.1)
            except Exception as e:
                print(f"处理客户端连接错误：{e}")
                time.sleep(0.1)
    
    def handle_client_request(self, client_socket):
        """处理单个客户端请求"""
        try:
            client_socket.setblocking(True)  # 设置为阻塞模式处理数据
            data = client_socket.recv(1024).decode()
            if data == "get_frames":
                with self.frame_lock:
                    if self.current_frame is not None and self.current_depth_frame is not None:
                        frames_data = {
                            'color': self.current_frame.copy(),  # 创建副本
                            'depth': self.current_depth_frame.copy(),
                            'intrinsics': self.color_intr
                        }
                        frames_packed = pickle.dumps(frames_data)
                        client_socket.sendall(struct.pack(">L", len(frames_packed)))
                        client_socket.sendall(frames_packed)
        except Exception as e:
            print(f"处理客户端请求错误：{e}")
        finally:
            client_socket.close()
    
    def capture_frames(self):
        """在独立线程中捕获图像"""
        while self.is_running:
            try:
                frames = self.pipeline.wait_for_frames()
                depth_frame = frames.get_depth_frame()
                color_frame = frames.get_color_frame()
                
                if not depth_frame or not color_frame:
                    continue
                
                depth_image = np.asanyarray(depth_frame.get_data())
                color_image = np.asanyarray(color_frame.get_data())
                
                with self.frame_lock:
                    self.current_frame = color_image
                    self.current_depth_frame = depth_image
                
            except Exception as e:
                print(f"捕获帧错误: {e}")
                time.sleep(0.03)
    
    def update_gui(self):
        """更新GUI显示"""
        try:
            with self.frame_lock:
                if self.current_frame is not None:
                    color_image_display = cv2.cvtColor(self.current_frame, cv2.COLOR_BGR2RGB)

                    image = Image.fromarray(color_image_display)
                    photo = ImageTk.PhotoImage(image=image)
                    self.video_label.configure(image=photo)
                    self.video_label.image = photo
        except Exception as e:
            print(f"更新GUI错误: {e}")
        
        self.parent.after(30, self.update_gui)
    
    def __del__(self):
        self.is_running = False
        if hasattr(self, 'pipeline'):
            self.pipeline.stop()
        if hasattr(self, 'server_socket'):
            self.server_socket.close()

def run_script(script_name):
    """在新线程中执行对应的 Python 脚本"""
    def run():
        try:
            script_path = os.path.join(CURRENT_DIR, script_name)
            if not os.path.exists(script_path):
                print(f"脚本文件不存在: {script_path}")
                return

            print(f"正在启动脚本: {script_name}")
            # 使用当前 Python 解释器启动子进程，避免阻塞主线程
            # 不重定向输出，允许脚本正常显示信息
            proc = subprocess.Popen([sys.executable, script_path],
                                    cwd=CURRENT_DIR,
                                    stdout=None,
                                    stderr=None)

            print(f"Started script {script_name}, pid={proc.pid}")

            # 等待一段时间检查进程是否正常启动
            try:
                proc.wait(timeout=5.0)  # 等待5秒
                if proc.returncode == 0:
                    print(f"脚本 {script_name} 执行完成")
                else:
                    print(f"脚本 {script_name} 执行失败，返回码: {proc.returncode}")
            except subprocess.TimeoutExpired:
                print(f"脚本 {script_name} 正在后台运行")
                # 让进程继续在后台运行

        except FileNotFoundError:
            print(f"Python解释器或脚本文件未找到: {script_name}")
        except Exception as e:
            print(f"启动脚本时出错 {script_name}: {e}")

    # 在独立线程中启动子进程（线程结束不会影响子进程）
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

def baseto0():
    def run_in_thread():
        global baseCtl
        if baseCtl is None:
            print("RobotController instance is not initialized.")
            return
        
        # 使用全局的 controller 实例移动到底盘0位
        baseCtl.move_to_position(0, 0)

    # 启动新线程运行机器人控制逻辑
    thread = threading.Thread(target=run_in_thread)
    thread.start()
    print("Started thread for moving to position (0, 0)")

def baseF0ToW1():
    def run_in_thread():
        global baseCtl
        if baseCtl is None:
            print("RobotController instance is not initialized.")
            return  
        # 使用全局的 controller 实例移动到底盘0位
        baseCtl.move_to_position(1, 0)
        time.sleep(1)
        baseCtl.move_slowly(0.2)
    # 启动新线程运行机器人控制逻辑
    thread = threading.Thread(target=run_in_thread)
    thread.start()
    print("Started thread for moving to position (0, 0)")
     
def baseFW1ToW2():
    def run_in_thread():
        global baseCtl
        if baseCtl is None:
            print("RobotController instance is not initialized.")
            return  
        # 使用全局的 controller 实例移动到底盘0位
        baseCtl.move_slowly(0)
        baseCtl.move_to_position(3, 1)
        time.sleep(1)
        baseCtl.move_slowly(0.22)
    # 启动新线程运行机器人控制逻辑
    thread = threading.Thread(target=run_in_thread)
    thread.start()
    print("Started thread for moving to position (0, 0)")

def baseFW2To0():
    def run_in_thread():
        global baseCtl
        if baseCtl is None:
            print("RobotController instance is not initialized.")
            return  
        # 使用全局的 controller 实例移动到底盘0位
        baseCtl.move_slowly(0.07)
        baseCtl.move_to_position(0, 0)
    # 启动新线程运行机器人控制逻辑
    thread = threading.Thread(target=run_in_thread)
    thread.start()
    print("Started thread for moving to position (0, 0)")
    
def baseF2To0():
    def run_in_thread():
        global baseCtl
        if baseCtl is None:
            print("RobotController instance is not initialized.")
            return  
        # 使用全局的 controller 实例移动到底盘0位
        baseCtl.move_to_position(0, 0)
# 启动GUI程序


# 创建 GUI 窗口
def create_gui(runtime=None):
    runtime = runtime or RobotRuntime()
    init_success = runtime.initialize()

    root = tk.Tk()
    root.title("机械臂操控系统")
    

    root.geometry('900x1400') 
    root.minsize(800, 1200)  
    root.configure(bg='#f0f2f5')

    # 专业配色方案
    PRIMARY_COLOR = '#2c3e50'
    SECONDARY_COLOR = '#3498db'
    SUCCESS_COLOR = '#27ae60'
    WARNING_COLOR = '#f39c12'
    DANGER_COLOR = '#e74c3c'
    LIGHT_BG = '#ecf0f1'
    DARK_TEXT = '#2c3e50'
    
    # 统一控件样式
    button_style = {
        'font': ('WenQuanYi Micro Hei', 10, 'bold'),
        'width': 14,
        'height': 1,
        'bg': SECONDARY_COLOR,
        'fg': 'white',
        'activebackground': '#2980b9',
        'relief': 'groove',
        'bd': 2,
        'padx': 5,
        'pady': 5
    }
    
    title_style = {
        'font': ('WenQuanYi Micro Hei', 12, 'bold'),
        'bg': LIGHT_BG,
        'fg': DARK_TEXT,
        'padx': 10,
        'pady': 5
    }
    
    frame_style = {
        'bg': LIGHT_BG,
        'padx': 10,
        'pady': 10,
        'highlightthickness': 1,
        'highlightbackground': '#bdc3c7'
    }

    # 创建主容器 - 使用网格布局（竖屏布局）
    main_frame = tk.Frame(root, bg=LIGHT_BG)
    main_frame.pack(fill='both', expand=True, padx=10, pady=10)
    
    # 配置网格权重 - 竖屏布局：上部分摄像头，下部分左侧内容，右侧面板
    main_frame.columnconfigure(0, weight=2)  # 左侧（摄像头+原左侧内容）
    main_frame.columnconfigure(1, weight=1)  # 右侧
    main_frame.rowconfigure(0, weight=0)     # 上部分（摄像头）
    main_frame.rowconfigure(1, weight=1)     # 下部分（原左侧内容）
    
    # 创建布局：上部分摄像头，下部分原左侧内容，右侧面板
    center_panel = tk.Frame(main_frame, bg='black')  # 黑色背景更适合视频
    center_panel.grid(row=0, column=0, sticky='nsew', padx=(0, 5), pady=(0, 5))
    
    left_panel = tk.Frame(main_frame, **frame_style)
    left_panel.grid(row=1, column=0, sticky='nsew', padx=(0, 5))
    
    right_panel = tk.Frame(main_frame, **frame_style)
    right_panel.grid(row=0, column=1, rowspan=2, sticky='nsew', padx=(5, 0))
    
    # 状态栏
    status_bar = tk.Frame(root, bg=PRIMARY_COLOR, height=24)
    status_bar.pack(fill='x', side='bottom')
    status_text = "机器人系统就绪" if init_success else "机器人初始化失败，请检查日志"
    status_label = tk.Label(status_bar, text=status_text, fg='white', bg=PRIMARY_COLOR, font=('WenQuanYi Micro Hei', 9))
    status_label.pack(side='left', padx=10)
    
    # 在中间添加摄像头预览
    camera_frame = CameraFrame(center_panel)
    camera_frame.pack(expand=True, fill='both')
    
    # 辅助函数 - 创建带图标的标题
    def create_section(parent, title):
        frame = tk.Frame(parent, bg=LIGHT_BG)
        title_frame = tk.Frame(frame, bg=LIGHT_BG)
        tk.Label(title_frame, text=title, **title_style).pack(side='left')
        title_frame.pack(fill='x', pady=(0, 5))
        return frame
    
    # 辅助函数 - 创建按钮网格
    def create_button_grid(parent, buttons, columns=2):
        frame = tk.Frame(parent, bg=LIGHT_BG)
        for i, (text, script) in enumerate(buttons):
            row, col = divmod(i, columns)
            btn = tk.Button(frame, text=text, command=lambda s=script: run_script(s), **button_style)
            btn.grid(row=row, column=col, padx=5, pady=5, sticky='ew')
        return frame

    # 使用运行时内的 controller/robot 在本进程执行抓瓶子，避免重复初始化硬件
    def run_grab_via_runtime():
        def worker():
            try:
                # 延迟导入以减小启动开销
                import grab_pingzi_baizhuo as grab_mod
                controller = runtime.controller
                robot = runtime.robot1
                if controller is None or robot is None:
                    print("controller 或 robot 未就绪")
                    return
                print("开始抓瓶子（使用运行时 controller）")
                result = grab_mod.capture_and_move(controller, robot)
                print("抓瓶子结果:", result)
            except Exception as e:
                print("抓瓶子出错:", e)
        threading.Thread(target=worker, daemon=True).start()

    # 左侧面板 - 机械臂控制
    arm_control_frame = create_section(left_panel, "机械臂控制")
    arm_control_frame.pack(fill='x', pady=(0, 10))
    # 抓瓶子：使用运行时内的 controller（避免重复初始化），在后台线程执行
    grab_button = tk.Button(arm_control_frame, text="抓瓶子", command=run_grab_via_runtime, **button_style)
    grab_button.pack(fill='x', pady=3)
    
    # 轨迹控制
    path_frame = create_section(left_panel, "轨迹控制")
    path_frame.pack(fill='x')
    
    path_buttons = [
        ("录制轨迹", "Path/Record_Path.py"),
        ("回轨迹起点", "Path/Path_Begin.py"),
    ]
    create_button_grid(path_frame, path_buttons).pack(fill='x')

    # 右侧面板 - 末端控制
    end_effector_frame = create_section(right_panel, "末端工具控制")
    end_effector_frame.pack(fill='x', pady=(0, 10))
    
    # 弹出枪头按钮 (直接调用YIYEQIANG_OUT模块)
    def eject_tip_action():
        def worker():
            try:
                from ..devices import yiyeqiang_out as yiyeqiang
                result = yiyeqiang.eject_tip(port='COM4')
                if result:
                    root.after(0, lambda: messagebox.showinfo("成功", "枪头已弹出!"))
                else:
                    root.after(0, lambda: messagebox.showerror("失败", "弹出枪头失败"))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("错误", f"执行出错: {str(e)}"))
        threading.Thread(target=worker, daemon=True).start()
    
    eject_btn = tk.Button(end_effector_frame, text="弹出枪头", command=eject_tip_action, **button_style)
    eject_btn.pack(fill='x', pady=3)
    
    # 初始化枪头按钮
    def init_tip_action():
        def worker():
            try:
                from ..devices import yiyeqiang_init
                result = yiyeqiang_init.init_tip(port='/dev/hand')
                if result:
                    root.after(0, lambda: messagebox.showinfo("成功", "枪头初始化成功!"))
                else:
                    root.after(0, lambda: messagebox.showerror("失败", "枪头初始化失败"))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("错误", f"执行出错: {str(e)}"))
        threading.Thread(target=worker, daemon=True).start()
    
    init_btn = tk.Button(end_effector_frame, text="初始化枪头", command=init_tip_action, **button_style)
    init_btn.pack(fill='x', pady=3)
    
    end_buttons = [
        ("打开夹爪", "release.py"),
        ("合上夹爪", "grab.py"),
        ("快换手解锁", "Kuaihuanshou_unlock.py"),
        ("快换手上锁", "Kuaihuanshou_lock.py")
    ]
    create_button_grid(end_effector_frame, end_buttons).pack(fill='x')
    
    # 锁枪控制
    lock_frame = create_section(right_panel, "锁枪控制")
    lock_frame.pack(fill='x', pady=(0, 10))
    
    lock_buttons = [
        ("取用Y1", "YIYEQIANG_QU_Y1.py"),
        ("放回Y1", "YIYEQIANG_FANG_Y1.py"),
        ("取用Y2", "YIYEQIANG_QU_Y2.py"),
        ("放回Y2", "YIYEQIANG_FANG_Y2.py")
    ]
    create_button_grid(lock_frame, lock_buttons).pack(fill='x')
    
    # 继电器控制
    relay_frame = create_section(right_panel, "继电器控制")
    relay_frame.pack(fill='x')
    
    relay_buttons = [
        ("Y1锁紧", "Y1_SUO.py"),
        ("Y1解锁", "Y1_JIE.py"),
        ("Y2锁紧", "Y2_SUO.py"),
        ("Y2解锁", "Y2_JIE.py")
    ]
    create_button_grid(relay_frame, relay_buttons).pack(fill='x')

    # Modbus 电机控制
    motor_frame = create_section(right_panel, "底座电机控制")
    motor_frame.pack(fill='x', pady=(10, 0))
    motor_panel = MotorControlPanel(motor_frame, button_style)

    def create_button_grid_2(parent, buttons,columns=2):
        frame = tk.Frame(parent, bg=LIGHT_BG)
        for i, (text, command) in enumerate(buttons):
            row, col = divmod(i, columns)
            btn = tk.Button(frame, text=text, command=command)
            btn.grid(row=row, column=col, padx=5, pady=5, sticky='ew')
        return frame
        # 继电器控制
    cfg_frame = create_section(right_panel, "机械臂配置")
    cfg_frame.pack(fill='x')
    
    robotcfg_buttons = [
        ("底盘0位", baseto0),
        ("底盘1位", baseF0ToW1),
        ("底盘2位", baseFW1ToW2),
        ("底盘2-0位", baseFW2To0),
    ]
    create_button_grid_2(cfg_frame, robotcfg_buttons).pack(fill='x')

    # 在关闭窗口时停止服务器
    def on_closing():
        print("关闭窗口，正在停止服务器...")
        motor_panel.shutdown()
        runtime.shutdown()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)

    root.mainloop()

if __name__ == "__main__":
    runtime = RobotRuntime()
    create_gui(runtime)