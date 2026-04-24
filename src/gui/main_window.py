import time
from pathlib import Path
from typing import List
import math
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                            QSplitter, QMessageBox, QFileDialog, QMenu,
                            QTabWidget, QPushButton, QLabel, QFrame, QApplication,
                            QInputDialog)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QAction, QPalette, QColor

from ..core.models import ActionDefinition, ActionType, SequenceItem, SequenceItemStatus
from ..widgets import ActionListWidget, SequenceListWidget, ControlPanel, LogWidget
from ..widgets.ai_assistant import AIAssistantWidget
from .dialogs import ActionConfigDialog
from ..core.storage import StorageManager
from .execution import ExecutionThread

# 尝试导入机械臂控制模块
try:
    from ..arm_sdk import RobotController
    from ..devices import ModbusMotor
    from ..devices.yiyeqiang_init import init_tip as YIYEQIANG_INIT
    from ..devices.yiyeqiang_out import eject_tip as YIYEQIANG_EJECT
    ROBOT_AVAILABLE = RobotController is not None
    MODBUS_AVAILABLE = ModbusMotor is not None
except ImportError as e:
    ROBOT_AVAILABLE = False
    MODBUS_AVAILABLE = False
    RobotController = None
    ModbusMotor = None
    YIYEQIANG_INIT = None
    YIYEQIANG_EJECT = None
    print(f"机械臂模块导入失败: {e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.actions: dict[ActionType, list[ActionDefinition]] = {
            ActionType.MOVE: [],
            ActionType.BASE_MOVE: [],
            ActionType.MANIPULATE: [],
            ActionType.INSPECT: [],
            ActionType.WAIT: [],
            ActionType.CHANGE_GUN: [],
            ActionType.VISION_CAPTURE: []
        }
        self.execution_thread: ExecutionThread = None
        self.is_paused = False

        # 机械臂控制相关
        self.robot_controller = None
        self.robot1_connected = False
        self.robot2_connected = False

        # 身体（ModbusMotor）控制相关
        self.body_controller = None
        self.body_connected = False

        # 底盘移动控制器
        self.move_controller = None

        # ADP（吸液枪）实例 - 在执行吸液/吐液时才创建
        self.adp_instance = None
        self.robot_pose_cache = {"robot1": None, "robot2": None}
        self.pose_timer = None

        self.init_ui()
        self.load_actions()

        # 设置 AI助手的主窗口引用（用于执行桥接器）
        if hasattr(self, 'ai_assistant_widget'):
            self.ai_assistant_widget.set_main_window(self)

        # 自动初始化机械臂和移液枪
        if ROBOT_AVAILABLE:
            self.auto_initialize()
        # 注释掉下面 2 行，防止启动时升降平台高度变化
        if MODBUS_AVAILABLE:
            self.initialize_body()
        self.initialize_pipette_on_startup()

    def init_ui(self):
        self.setWindowTitle("Robot Action Orchestrator")
        self.setMinimumSize(540, 800)
        self.resize(540, 960)

        self.create_menu()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 顶部：设备状态栏（加高，双行显示）
        self.status_bar = self.create_status_bar()
        layout.addWidget(self.status_bar)

        # 底部：横向 Splitter，左=动作库，右=序列+控制+日志
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = self.create_left_panel()
        right_panel = self.create_right_panel()

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter, stretch=1)

    def create_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")

        save_task_action = QAction("保存任务序列", self)
        save_task_action.setShortcut("Ctrl+S")
        save_task_action.triggered.connect(self.save_task)
        file_menu.addAction(save_task_action)

        load_task_action = QAction("加载任务序列", self)
        load_task_action.setShortcut("Ctrl+O")
        load_task_action.triggered.connect(self.load_task)
        file_menu.addAction(load_task_action)

        file_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def create_left_panel(self) -> QWidget:
        """动作库面板：Tab横向标签 + 动作列表（受Splitter宽度控制）+ 按钮"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Tab 标签横向排列（标签在顶部）
        self.action_tabs = QTabWidget()
        self.action_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.action_tabs.setMovable(False)

        self.move_list = ActionListWidget()
        self.manipulate_list = ActionListWidget()
        self.inspect_list = ActionListWidget()
        self.change_gun_list = ActionListWidget()
        self.vision_capture_list = ActionListWidget()

        self.action_tabs.addTab(self.move_list, "移动类")
        self.action_tabs.addTab(self.manipulate_list, "执行类")
        self.action_tabs.addTab(self.inspect_list, "检测类")
        self.action_tabs.addTab(self.change_gun_list, "换枪类")
        self.action_tabs.addTab(self.vision_capture_list, "视觉类")

        # AI助手 Tab
        self.ai_assistant_widget = AIAssistantWidget()
        self.action_tabs.addTab(self.ai_assistant_widget, "🤖 AI助手")

        layout.addWidget(self.action_tabs, stretch=1)

        # 底部按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)
        self.create_btn = QPushButton("新建动作")
        self.create_btn.setMinimumHeight(32)
        self.create_btn.clicked.connect(self.create_action)
        self.edit_btn = QPushButton("修改动作")
        self.edit_btn.setMinimumHeight(32)
        self.edit_btn.clicked.connect(self.edit_action)
        self.delete_btn = QPushButton("删除动作")
        self.delete_btn.setMinimumHeight(32)
        self.delete_btn.clicked.connect(self.delete_action)
        btn_layout.addWidget(self.create_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.delete_btn)

        self.test_camera_btn = QPushButton("测试相机")
        self.test_camera_btn.setMinimumHeight(32)
        self.test_camera_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.test_camera_btn.clicked.connect(self.test_camera)
        btn_layout.addWidget(self.test_camera_btn)
        layout.addLayout(btn_layout)

        return panel

    def create_right_panel(self) -> QWidget:
        """右侧面板：序列列表 + 控制面板 + 日志，竖向堆叠"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 序列列表：自占剩余空间（横向卡片区需保证最小高度，避免被挤没）
        self.sequence_list = SequenceListWidget()
        self.sequence_list.setMinimumHeight(140)
        layout.addWidget(self.sequence_list, stretch=2)

        self.pose_panel = self.create_pose_panel()
        layout.addWidget(self.pose_panel)

        self.basic_control_panel = self.create_basic_control_panel()
        layout.addWidget(self.basic_control_panel)

        # 控制面板
        self.control_panel = ControlPanel()
        self.control_panel.start_clicked.connect(self.start_execution)
        self.control_panel.pause_clicked.connect(self.toggle_pause)
        self.control_panel.stop_clicked.connect(self.stop_execution)
        self.control_panel.move_up_clicked.connect(self.move_item_up)
        self.control_panel.move_down_clicked.connect(self.move_item_down)
        self.control_panel.edit_clicked.connect(self.edit_sequence_item)
        self.control_panel.delete_clicked.connect(self.delete_item)
        self.control_panel.clear_clicked.connect(self.clear_sequence)
        self.control_panel.save_clicked.connect(self.save_task)
        self.control_panel.load_clicked.connect(self.load_task)
        layout.addWidget(self.control_panel)

        # 日志
        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        return panel

    def create_pose_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        title = QLabel("Arm Pose")
        title.setStyleSheet("font-size: 12px; font-weight: bold;")
        self.refresh_pose_btn = QPushButton("Refresh")
        self.refresh_pose_btn.setFixedHeight(24)
        self.refresh_pose_btn.clicked.connect(self.refresh_arm_poses)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_pose_btn)
        layout.addLayout(header_layout)

        self.robot1_pose_value_label, self.copy_robot1_pose_btn = self._build_pose_row(layout, "R1")
        self.robot2_pose_value_label, self.copy_robot2_pose_btn = self._build_pose_row(layout, "R2")

        self.pose_timer = QTimer(self)
        self.pose_timer.setInterval(1000)
        self.pose_timer.timeout.connect(self.refresh_arm_poses)
        self.pose_timer.start()

        return panel

    def create_basic_control_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        title = QLabel("Basic Control")
        title.setStyleSheet("font-size: 12px; font-weight: bold;")
        layout.addWidget(title)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self.gripper_open_btn = QPushButton("Gripper Open")
        self.gripper_open_btn.setMinimumHeight(28)
        self.gripper_open_btn.clicked.connect(self.on_gripper_open_clicked)

        self.gripper_close_btn = QPushButton("Gripper Close")
        self.gripper_close_btn.setMinimumHeight(28)
        self.gripper_close_btn.clicked.connect(self.on_gripper_close_clicked)

        self.init_pipette_btn = QPushButton("退枪头")
        self.init_pipette_btn.setMinimumHeight(28)
        self.init_pipette_btn.clicked.connect(self.eject_pipette_tip)

        btn_layout.addWidget(self.gripper_open_btn)
        btn_layout.addWidget(self.gripper_close_btn)
        btn_layout.addWidget(self.init_pipette_btn)
        layout.addLayout(btn_layout)

        self.update_basic_control_buttons()
        return panel

    def update_basic_control_buttons(self):
        gripper_ready = self.robot_controller is not None and self.robot1_connected
        if hasattr(self, "gripper_open_btn"):
            self.gripper_open_btn.setEnabled(gripper_ready)
        if hasattr(self, "gripper_close_btn"):
            self.gripper_close_btn.setEnabled(gripper_ready)

    def on_gripper_open_clicked(self):
        if self.robot_controller is None or not self.robot1_connected:
            QMessageBox.warning(self, "Warning", "Robot1 is not connected")
            return

        try:
            success = self.robot_controller.gripper_open_robot1()
            if success:
                self.log_widget.append_log("Robot1 gripper opened")
            else:
                QMessageBox.warning(self, "Warning", "Failed to open gripper")
                self.log_widget.append_log("Robot1 gripper open failed")
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Gripper open error: {e}")
            self.log_widget.append_log(f"Robot1 gripper open error: {e}")

    def on_gripper_close_clicked(self):
        if self.robot_controller is None or not self.robot1_connected:
            QMessageBox.warning(self, "Warning", "Robot1 is not connected")
            return

        try:
            success = self.robot_controller.gripper_close_robot1()
            if success:
                self.log_widget.append_log("Robot1 gripper closed")
            else:
                QMessageBox.warning(self, "Warning", "Failed to close gripper")
                self.log_widget.append_log("Robot1 gripper close failed")
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Gripper close error: {e}")
            self.log_widget.append_log(f"Robot1 gripper close error: {e}")

    def _build_pose_row(self, parent_layout: QVBoxLayout, robot_label: str):
        row = QHBoxLayout()
        row_label = QLabel(f"{robot_label}:")
        row_label.setFixedWidth(28)
        row_label.setStyleSheet("font-weight: bold;")

        pose_label = QLabel("--")
        pose_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        copy_btn = QPushButton("Copy")
        copy_btn.setFixedHeight(24)
        copy_btn.clicked.connect(lambda _, name=robot_label.lower().replace("r", "robot"): self.copy_robot_pose(name))

        row.addWidget(row_label)
        row.addWidget(pose_label, stretch=1)
        row.addWidget(copy_btn)
        parent_layout.addLayout(row)

        return pose_label, copy_btn

    def refresh_arm_poses(self):
        self._refresh_single_robot_pose("robot1")
        self._refresh_single_robot_pose("robot2")

    def _refresh_single_robot_pose(self, robot_name: str):
        pose = self._get_current_pose(robot_name)
        label = self.robot1_pose_value_label if robot_name == "robot1" else self.robot2_pose_value_label

        if pose is None:
            self.robot_pose_cache[robot_name] = None
            label.setText("--")
            return

        self.robot_pose_cache[robot_name] = pose
        label.setText(self.format_pose_text(pose))

    def _get_current_pose(self, robot_name: str):
        if self.robot_controller is None:
            return None

        ctrl = getattr(self.robot_controller, f"{robot_name}_ctrl", None)
        robot = getattr(ctrl, "robot", None)
        if robot is None:
            return None

        try:
            ret, state = robot.rm_get_current_arm_state()
            if ret != 0:
                return None
            pose = state.get("pose")
            if not isinstance(pose, (list, tuple)) or len(pose) < 6:
                return None
            return [float(v) for v in pose[:6]]
        except Exception:
            return None

    def format_pose_text(self, pose):
        x_mm = pose[0] * 1000
        y_mm = pose[1] * 1000
        z_mm = pose[2] * 1000
        rx_deg = math.degrees(pose[3])
        ry_deg = math.degrees(pose[4])
        rz_deg = math.degrees(pose[5])
        return (
            f"X:{x_mm:.1f} Y:{y_mm:.1f} Z:{z_mm:.1f} mm | "
            f"RX:{rx_deg:.1f} RY:{ry_deg:.1f} RZ:{rz_deg:.1f} deg"
        )

    def copy_robot_pose(self, robot_name: str):
        pose = self.robot_pose_cache.get(robot_name)
        if pose is None:
            self._refresh_single_robot_pose(robot_name)
            pose = self.robot_pose_cache.get(robot_name)

        if pose is None:
            QMessageBox.warning(self, "Warning", f"{robot_name.upper()} pose is unavailable")
            return

        pose_text = f"[{', '.join([f'{v:.6f}' for v in pose])}]"
        QApplication.clipboard().setText(pose_text)
        self.log_widget.append_log(f"Copied {robot_name.upper()} pose: {pose_text}")

    def create_status_bar(self) -> QWidget:
        """设备状态栏：竖向两行，每行四个设备"""
        bar = QFrame()
        bar.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        bar.setMinimumHeight(72)
        bar.setMaximumHeight(90)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        title = QLabel("设备状态")
        title.setStyleSheet("font-size: 12px; font-weight: bold;")
        layout.addWidget(title)

        status_layout = QHBoxLayout()
        status_layout.setSpacing(16)

        def make_status_item(label_text: str, indicator_name: str):
            """创建 [圆点 + 文字] 的水平组合"""
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(4)
            indicator = QLabel()
            indicator.setFixedSize(16, 16)
            indicator.setStyleSheet("background-color: #555; border-radius: 8px;")
            indicator.setObjectName(indicator_name + "_indicator")
            text = QLabel(label_text)
            text.setObjectName(indicator_name + "_status_text")
            text.setStyleSheet("font-size: 12px;")
            item_layout.addWidget(indicator)
            item_layout.addWidget(text)
            item_layout.addStretch()
            return item_widget, indicator, text

        r1_widget, self.robot1_status_indicator, r1_text = make_status_item("R1: 未连接", "robot1")
        r1_text.setObjectName("robot1_status_text")
        r2_widget, self.robot2_status_indicator, r2_text = make_status_item("R2: 未连接", "robot2")
        r2_text.setObjectName("robot2_status_text")
        body_widget, self.body_status_indicator, body_text = make_status_item("身体: 未连接", "body")
        body_text.setObjectName("body_status_text")
        pip_widget, self.pipette_status_indicator, pip_text = make_status_item("移液枪: 未初始化", "pipette")
        pip_text.setObjectName("pipette_status_text")

        # 存储文本标签引用，供 update_* 方法直接使用
        self.robot1_status_text = r1_text
        self.robot2_status_text = r2_text
        self.body_status_text = body_text
        self.pipette_status_text = pip_text

        status_layout.addWidget(r1_widget)
        status_layout.addWidget(r2_widget)
        status_layout.addWidget(body_widget)
        status_layout.addWidget(pip_widget)
        status_layout.addStretch()

        layout.addLayout(status_layout)
        return bar

    def auto_initialize(self):
        """自动初始化机械臂"""
        # 只自动初始化机械臂
        self.initialize_robots()

    def initialize_robots(self):
        """初始化机械臂"""
        if not ROBOT_AVAILABLE:
            self.log_widget.append_log("机械臂模块不可用")
            return

        self.log_widget.append_log("开始初始化机械臂...")

        try:
            # 创建机械臂控制器
            self.robot_controller = RobotController()
            
            # 创建底盘移动控制器
            try:
                from ..move.move_controller import RobotMoveController
                self.move_controller = RobotMoveController()
                self.log_widget.append_log("底盘移动控制器初始化成功")
            except Exception as e:
                self.log_widget.append_log(f"底盘移动控制器初始化失败：{e}")

            # 初始化 Robot1
            self.log_widget.append_log("初始化 Robot1...")
            robot1 = self.robot_controller.init_robot1()
            if robot1 is not None:
                # success1 = self.robot_controller.spawn_robot1(robot1)
                success1 =True
                if success1:
                    self.robot1_connected = True
                    self.update_robot_status("robot1", True)
                    self.log_widget.append_log("Robot1 初始化成功")
                else:
                    self.log_widget.append_log("Robot1 移动到初始位置失败")
            else:
                self.log_widget.append_log("Robot1 初始化失败")

            # 初始化 Robot2
            self.log_widget.append_log("初始化 Robot2...")
            robot2 = self.robot_controller.init_robot2()
            if robot2 is not None:
                # success2 = self.robot_controller.spawn_robot2(robot2)
                success2 =True
                if success2:
                    self.robot2_connected = True
                    self.update_robot_status("robot2", True)
                    self.log_widget.append_log("Robot2 初始化成功")
                else:
                    self.log_widget.append_log("Robot2 移动到初始位置失败")
            else:
                self.log_widget.append_log("Robot2 初始化失败")

            # 更新状态
            self.refresh_arm_poses()

            if self.robot1_connected and self.robot2_connected:
                self.log_widget.append_log("机械臂初始化完成")
            else:
                self.log_widget.append_log("机械臂初始化部分失败，请检查连接")

        except Exception as e:
            self.log_widget.append_log(f"机械臂初始化异常: {str(e)}")

    def update_robot_status(self, robot_name: str, connected: bool):
        """更新机械臂状态指示灯"""
        if robot_name == "robot1":
            indicator = self.robot1_status_indicator
            status_text = self.robot1_status_text
        else:
            indicator = self.robot2_status_indicator
            status_text = self.robot2_status_text

        if connected:
            indicator.setStyleSheet("background-color: #28a745; border-radius: 8px;")
            status_text.setText("已连接")
        else:
            indicator.setStyleSheet("background-color: #dc3545; border-radius: 8px;")
            status_text.setText("未连接")

        self.update_basic_control_buttons()

    def update_pipette_status(self, initialized: bool):
        """更新移液枪状态指示灯"""
        if initialized:
            self.pipette_status_indicator.setStyleSheet("background-color: #28a745; border-radius: 8px;")
            self.pipette_status_text.setText("已初始化")
        else:
            self.pipette_status_indicator.setStyleSheet("background-color: #dc3545; border-radius: 8px;")
            self.pipette_status_text.setText("未初始化")

    def initialize_pipette(self):
        """Initialize pipette by YIYEQIANG_INIT."""
        self.log_widget.append_log("Starting pipette initialization...")
        if YIYEQIANG_INIT is None:
            self.log_widget.append_log("Pipette init module is unavailable")
            QMessageBox.warning(self, "Warning", "Pipette init module is unavailable")
            self.update_pipette_status(False)
            return

        self.init_pipette_btn.setEnabled(False)
        try:
            success = YIYEQIANG_INIT(port='/dev/hand')
            self.update_pipette_status(bool(success))
            if success:
                self.log_widget.append_log("Pipette initialized successfully")
            else:
                self.log_widget.append_log("Pipette initialization failed")
                QMessageBox.warning(self, "Warning", "Pipette initialization failed, please check serial port/device")
        except Exception as e:
            self.update_pipette_status(False)
            self.log_widget.append_log(f"Pipette initialization error: {str(e)}")
            QMessageBox.warning(self, "Warning", f"Pipette initialization error: {e}")
        finally:
            self.init_pipette_btn.setEnabled(True)

    def initialize_pipette_on_startup(self):
        """Initialize pipette automatically when app starts."""
        self.log_widget.append_log("Auto initializing pipette...")
        if YIYEQIANG_INIT is None:
            self.log_widget.append_log("Pipette init module is unavailable")
            self.update_pipette_status(False)
            return

        try:
            success = YIYEQIANG_INIT(port='/dev/hand')
            self.update_pipette_status(bool(success))
            if success:
                self.log_widget.append_log("Pipette initialized successfully")
            else:
                self.log_widget.append_log("Pipette initialization failed")
        except Exception as e:
            self.update_pipette_status(False)
            self.log_widget.append_log(f"Pipette initialization error: {str(e)}")

    def eject_pipette_tip(self):
        """Eject pipette tip manually."""
        if YIYEQIANG_EJECT is None:
            self.log_widget.append_log("Pipette eject module is unavailable")
            QMessageBox.warning(self, "Warning", "Pipette eject module is unavailable")
            return

        self.init_pipette_btn.setEnabled(False)
        try:
            self.log_widget.append_log("Ejecting pipette tip...")
            success = YIYEQIANG_EJECT(port='/dev/hand')
            if success:
                self.log_widget.append_log("Pipette tip ejected successfully")
            else:
                self.log_widget.append_log("Pipette tip eject failed")
                QMessageBox.warning(self, "Warning", "Failed to eject pipette tip")
        except Exception as e:
            self.log_widget.append_log(f"Pipette tip eject error: {str(e)}")
            QMessageBox.warning(self, "Warning", f"Pipette tip eject error: {e}")
        finally:
            self.init_pipette_btn.setEnabled(True)

    def initialize_body(self):
        """初始化身体（ModbusMotor）"""
        if not MODBUS_AVAILABLE:
            self.log_widget.append_log("身体模块不可用")
            return

        self.log_widget.append_log("开始初始化身体...")

        try:
            self.body_controller = ModbusMotor(port="/dev/body", baudrate=115200, slave_id=1, timeout=1)
            self.body_connected = True
            self.update_body_status(True)
            self.log_widget.append_log("身体初始化成功")
        except Exception as e:
            self.log_widget.append_log(f"身体初始化异常: {str(e)}")
            self.update_body_status(False)

    def update_body_status(self, connected: bool):
        """更新身体状态指示灯"""
        if connected:
            self.body_status_indicator.setStyleSheet("background-color: #28a745; border-radius: 8px;")
            self.body_status_text.setText("已连接")
        else:
            self.body_status_indicator.setStyleSheet("background-color: #dc3545; border-radius: 8px;")
            self.body_status_text.setText("未连接")

    def create_action(self):
        current_tab = self.action_tabs.currentIndex()
        action_type = self._resolve_action_type_for_current_tab(current_tab)
        if action_type is None:
            return

        dialog = ActionConfigDialog(action_type)
        if dialog.exec():
            action = dialog.get_action_definition()
            self.actions[action.type].append(action)
            self.refresh_action_list(action.type)
            self.save_actions()

    def delete_action(self):
        current_tab = self.action_tabs.currentIndex()
        
        # 移动类 Tab 需要特殊处理，因为包含多种类型
        if current_tab == 0:
            current_item = self.move_list.currentItem()
            if current_item is None:
                QMessageBox.warning(self, "警告", "请先选择一个要删除的动作")
                return
            
            action = current_item.data(Qt.ItemDataRole.UserRole)
            if action and action.type in self.actions:
                self.actions[action.type].remove(action)
                self.refresh_action_list(action.type)
                self.save_actions()
            return
        
        action_type_map = {
            1: ActionType.MANIPULATE,
            2: ActionType.INSPECT,
            3: ActionType.CHANGE_GUN,
            4: ActionType.VISION_CAPTURE
        }
        action_type = action_type_map.get(current_tab)
        if action_type is None:
            return

        list_map = {
            ActionType.MANIPULATE: self.manipulate_list,
            ActionType.INSPECT: self.inspect_list,
            ActionType.CHANGE_GUN: self.change_gun_list,
            ActionType.VISION_CAPTURE: self.vision_capture_list
        }
        action_list = list_map[action_type]

        current_item = action_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "警告", "请先选择一个要删除的动作")
            return

        action = current_item.data(Qt.ItemDataRole.UserRole)
        if action and action in self.actions[action.type]:
            self.actions[action.type].remove(action)
            self.refresh_action_list(action.type)
            self.save_actions()

    def edit_action(self):
        action_list = self._get_current_action_list_widget()
        if action_list is None:
            return

        current_item = action_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "Warning", "Please select an action to edit")
            return

        action = current_item.data(Qt.ItemDataRole.UserRole)
        if action is None:
            QMessageBox.warning(self, "Warning", "Cannot read selected action")
            return

        action_data = {
            "id": action.id,
            "name": action.name,
            "parameters": action.parameters
        }
        dialog = ActionConfigDialog(action.type, action_data, self)
        if not dialog.exec():
            return

        updated_action = dialog.get_action_definition()
        target_actions = self.actions[action.type]
        replaced = False

        for idx, existing in enumerate(target_actions):
            if existing.id == action.id:
                target_actions[idx] = updated_action
                replaced = True
                break

        if not replaced and action in target_actions:
            target_actions[target_actions.index(action)] = updated_action
            replaced = True

        if not replaced:
            QMessageBox.warning(self, "Warning", "Target action not found")
            return

        self.refresh_action_list(action.type)
        self.save_actions()

    def refresh_action_list(self, action_type: ActionType):
        if action_type in {ActionType.MANIPULATE, ActionType.WAIT}:
            self._refresh_execute_merged_list()
            return

        # 移动类的所有子类型都显示在 move_list 中
        if action_type in {ActionType.MOVE, ActionType.BASE_MOVE}:
            self.move_list.clear()
            for action in self.actions[ActionType.MOVE]:
                self.move_list.add_action(action)
            for action in self.actions[ActionType.BASE_MOVE]:
                self.move_list.add_action(action)
            return

        list_map = {
            ActionType.INSPECT: self.inspect_list,
            ActionType.CHANGE_GUN: self.change_gun_list,
            ActionType.VISION_CAPTURE: self.vision_capture_list
        }
        action_list = list_map[action_type]
        action_list.clear()

        for action in self.actions[action_type]:
            action_list.add_action(action)

    def save_actions(self):
        all_actions = []
        for action_type_actions in self.actions.values():
            all_actions.extend(action_type_actions)
        StorageManager.save_actions(all_actions)

    def load_actions(self):
        all_actions = StorageManager.load_actions()
        for action_type in self.actions:
            self.actions[action_type].clear()

        for action in all_actions:
            self.actions[action.type].append(action)

        for action_type in self.actions:
            self.refresh_action_list(action_type)

    def _refresh_execute_merged_list(self):
        self.manipulate_list.clear()
        for action in self.actions[ActionType.MANIPULATE]:
            self.manipulate_list.add_action(action)
        for action in self.actions[ActionType.WAIT]:
            self.manipulate_list.add_action(action)

    def _resolve_action_type_for_current_tab(self, current_tab: int):
        action_type_map = {
            0: ActionType.MOVE,  # 移动类 Tab，需要进一步选择
            2: ActionType.INSPECT,
            3: ActionType.CHANGE_GUN,
            4: ActionType.VISION_CAPTURE
        }
        if current_tab == 1:
            options = ["Manipulate", "Wait"]
            selected, ok = QInputDialog.getItem(
                self,
                "Select Action Type",
                "Create under Execute tab:",
                options,
                0,
                False
            )
            if not ok:
                return None
            return ActionType.WAIT if selected == "Wait" else ActionType.MANIPULATE
        
        # 移动类 Tab 需要选择具体类型
        if current_tab == 0:
            options = ["机械臂/身体移动", "底盘移动"]
            selected, ok = QInputDialog.getItem(
                self,
                "选择移动类型",
                "创建移动类动作:",
                options,
                0,
                False
            )
            if not ok:
                return None
            if selected == "机械臂/身体移动":
                return ActionType.MOVE
            else:
                return ActionType.BASE_MOVE

        return action_type_map.get(current_tab)

    def _get_current_action_list_widget(self):
        current_tab = self.action_tabs.currentIndex()
        tab_list_map = {
            0: self.move_list,
            1: self.manipulate_list,
            2: self.inspect_list,
            3: self.change_gun_list,
            4: self.vision_capture_list
        }
        return tab_list_map.get(current_tab)

    def save_task(self):
        sequence = self.sequence_list.get_sequence()
        if not sequence:
            QMessageBox.warning(self, "警告", "序列为空,无需保存")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "保存任务序列", "", "Task Files (*.task)"
        )
        if filename:
            task_name = Path(filename).name
            StorageManager.save_sequence(sequence, task_name)
            self.log_widget.append_log(f"任务已保存: {task_name}")

    def load_task(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "加载任务序列", str(StorageManager.TASKS_DIR), "Task Files (*.task)"
        )
        if filename:
            task_name = Path(filename).name
            sequence = StorageManager.load_sequence(task_name)
            self.sequence_list.clear()
            for item in sequence:
                self.sequence_list.add_sequence_item(item)
            self.log_widget.append_log(f"任务已加载: {task_name}")

    def start_execution(self):
        sequence = self.sequence_list.get_sequence()
        if not sequence:
            QMessageBox.warning(self, "警告", "请先添加动作到序列中")
            return

        self.log_widget.append_log("开始执行序列...")

        for item in sequence:
            item.status = SequenceItemStatus.PENDING

        for i, item in enumerate(sequence):
            self.sequence_list.update_item_status(i, item)

        self.execution_thread = ExecutionThread(sequence, self.robot_controller, self.body_controller, self.move_controller)
        self.execution_thread.step_started.connect(self.on_step_started)
        self.execution_thread.step_completed.connect(self.on_step_completed)
        self.execution_thread.step_failed.connect(self.on_step_failed)
        self.execution_thread.log_message.connect(self.log_widget.append_log)
        self.execution_thread.finished.connect(self.on_execution_finished)

        self.execution_thread.start()

    def toggle_pause(self):
        if self.execution_thread and self.execution_thread.isRunning():
            if self.is_paused:
                self.execution_thread.resume()
                self.control_panel.pause_btn.setText("暂停")
                self.log_widget.append_log("执行继续")
            else:
                self.execution_thread.pause()
                self.control_panel.pause_btn.setText("继续")
                self.log_widget.append_log("执行暂停")
            self.is_paused = not self.is_paused

    def stop_execution(self):
        if self.execution_thread and self.execution_thread.isRunning():
            self.execution_thread.stop()
            self.log_widget.append_log("紧急停止已触发")

    def on_execution_completed(self, success: bool):
        self.log_widget.append_log("AI 序列执行完成" if success else "AI 序列执行失败")
        self.is_paused = False
        self.control_panel.pause_btn.setText("暂停")

    def on_step_started(self, index: int, item: SequenceItem):
        self.sequence_list.update_item_status(index, item)
        self.sequence_list.scrollToItem(self.sequence_list.item(index))

    def on_step_completed(self, index: int, item: SequenceItem):
        self.sequence_list.update_item_status(index, item)

    def on_step_failed(self, index: int, item: SequenceItem, error_msg: str):
        self.sequence_list.update_item_status(index, item)
        QMessageBox.critical(self, "执行失败", f"步骤 {index + 1} 失败:\n{error_msg}")

    def on_execution_finished(self):
        self.log_widget.append_log("序列执行完成")
        self.is_paused = False
        self.control_panel.pause_btn.setText("暂停")

    def move_item_up(self):
        current_row = self.sequence_list.currentRow()
        if current_row > 0:
            item = self.sequence_list.takeItem(current_row)
            self.sequence_list.insertItem(current_row - 1, item)
            self.refresh_sequence_numbers(selected_row=current_row - 1)

    def move_item_down(self):
        current_row = self.sequence_list.currentRow()
        if current_row < self.sequence_list.count() - 1:
            item = self.sequence_list.takeItem(current_row)
            self.sequence_list.insertItem(current_row + 1, item)
            self.refresh_sequence_numbers(selected_row=current_row + 1)

    def delete_item(self):
        current_row = self.sequence_list.currentRow()
        if current_row >= 0:
            self.sequence_list.takeItem(current_row)
            next_row = min(current_row, self.sequence_list.count() - 1)
            self.refresh_sequence_numbers(selected_row=next_row)

    def edit_sequence_item(self):
        current_row = self.sequence_list.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select a sequence item to edit")
            return

        list_item = self.sequence_list.item(current_row)
        if list_item is None:
            QMessageBox.warning(self, "Warning", "Cannot read selected sequence item")
            return

        seq_item = list_item.data(Qt.ItemDataRole.UserRole)
        if seq_item is None:
            QMessageBox.warning(self, "Warning", "Cannot read selected sequence item")
            return

        action_def = seq_item.definition
        action_data = {
            "id": action_def.id,
            "name": action_def.name,
            "parameters": action_def.parameters,
        }
        dialog = ActionConfigDialog(action_def.type, action_data, self)
        if not dialog.exec():
            return

        updated_definition = dialog.get_action_definition()
        seq_item.definition = updated_definition
        self.sequence_list.update_item_status(current_row, seq_item)
        self.sequence_list.setCurrentRow(current_row)
        self.log_widget.append_log(f"已更新序列动作: {updated_definition.name}")

    def add_ai_sequence(
        self,
        sequence: List,
        replace: bool = False,
        stagger_interval_ms: int = 0,
    ):
        """将 AI 规划的动作同步到右侧序列区；replace=True 时先清空。
        stagger_interval_ms>0 时按间隔逐项出现（类似从左拖到右侧的观感），需与执行启动延迟配合。"""
        if not sequence:
            return
        normalized: list[SequenceItem] = []
        for raw in sequence:
            if isinstance(raw, dict):
                normalized.append(SequenceItem.from_dict(raw))
            else:
                normalized.append(raw)
        if replace:
            self.sequence_list.clear_sequence()
        if stagger_interval_ms <= 0:
            for item in normalized:
                self.sequence_list.add_sequence_item(item)
            for i, item in enumerate(normalized):
                item.status = SequenceItemStatus.PENDING
                self.sequence_list.update_item_status(i, item)
            self.log_widget.append_log(f"已同步执行序列到右侧，共 {len(normalized)} 个动作")
            return

        from PyQt6.QtCore import QTimer

        self.log_widget.append_log(
            f"正在将 {len(normalized)} 个动作载入右侧序列区（逐项显示）..."
        )

        for i, item in enumerate(normalized):
            item.status = SequenceItemStatus.PENDING

            def make_add(idx: int, seq_item: SequenceItem):
                def _add():
                    self.sequence_list.add_sequence_item(seq_item)
                    self.sequence_list.update_item_status(idx, seq_item)

                return _add

            QTimer.singleShot(stagger_interval_ms * i, make_add(i, item))

    def clear_sequence(self):
        reply = QMessageBox.question(
            self, "确认", "确定要清空所有序列吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.sequence_list.clear_sequence()
            self.log_widget.append_log("序列已清空")

    def refresh_sequence_numbers(self, selected_row: int | None = None):
        sequence = self.sequence_list.get_sequence()
        self.sequence_list.clear()
        for item in sequence:
            self.sequence_list.add_sequence_item(item)
        if selected_row is not None and 0 <= selected_row < self.sequence_list.count():
            self.sequence_list.setCurrentRow(selected_row)

    def test_camera(self):
        """
        在独立 QThread 中测试 RealSense pipeline。
        QThread 有自己的 Qt 事件循环，能正确处理 RealSense SDK 的 USB urb 回调。
        """
        self.test_camera_btn.setEnabled(False)
        self.test_camera_btn.setText("测试中...")

        class _TestWorker(QThread):
            result = pyqtSignal(bool, str)

            def run(self):
                import pyrealsense2 as rs
                from src.core.config_loader import Config

                sn = Config.get_instance().REALSENSE_DEVICE_SN

                try:
                    ctx = rs.context()
                    devices = list(ctx.devices)
                    if not devices:
                        self.result.emit(False, "未检测到 RealSense 设备")
                        return

                    pipeline = rs.pipeline()
                    cfg = rs.config()
                    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
                    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
                    if sn:
                        cfg.enable_device(sn)
                    profile = pipeline.start(cfg)

                    time.sleep(1)

                    deadline = time.time() + 10
                    while time.time() < deadline:
                        try:
                            frames = pipeline.wait_for_frames(200)
                            color = frames.get_color_frame()
                            depth = frames.get_depth_frame()
                            if color and depth:
                                msg = (f"SUCCESS: color={color.width}x{color.height}  "
                                       f"depth={depth.get_distance(320, 240):.3f}m  "
                                       f"(SN={sn or 'auto-select'})")
                                pipeline.stop()
                                self.result.emit(True, msg)
                                return
                        except Exception:
                            pass

                    pipeline.stop()
                    self.result.emit(False, "取帧超时（10 秒内未获得有效帧）")

                except Exception as e:
                    self.result.emit(False, str(e))

        def on_result(success, msg):
            self.log_widget.append_log(f"[相机测试] {msg}")
            self.test_camera_btn.setEnabled(True)
            self.test_camera_btn.setText("测试相机")

        self._camera_test_thread = _TestWorker()
        self._camera_test_thread.result.connect(on_result)
        self._camera_test_thread.start()

    def closeEvent(self, event):
        if self.pose_timer is not None:
            self.pose_timer.stop()

        if self.execution_thread and self.execution_thread.isRunning():
            self.execution_thread.stop()
            self.execution_thread.wait()

        # 断开机械臂连接
        if self.robot_controller is not None:
            try:
                self.robot_controller.shutdown()
                self.log_widget.append_log("机械臂已断开连接")
            except Exception as e:
                print(f"断开机械臂连接时出错: {e}")

        # 关闭身体连接
        if self.body_controller is not None:
            try:
                self.body_controller.close()
                self.log_widget.append_log("身体已断开连接")
            except Exception as e:
                print(f"断开身体连接时出错：{e}")

        # 关闭底盘移动控制器连接
        if self.move_controller is not None:
            try:
                self.move_controller.close()
                self.log_widget.append_log("底盘移动控制器已断开连接")
            except Exception as e:
                print(f"断开底盘移动控制器连接时出错：{e}")

        # 关闭 ADP 连接
        if self.adp_instance is not None:
            try:
                self.adp_instance.close()
                self.log_widget.append_log("吸液枪已关闭")
            except Exception as e:
                print(f"关闭吸液枪时出错: {e}")

        event.accept()
