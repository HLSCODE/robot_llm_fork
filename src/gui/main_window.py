import time
from pathlib import Path
from typing import List
import math
import json
from uuid import uuid4
from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                            QSplitter, QMessageBox, QFileDialog, QMenu,
                            QTabWidget, QPushButton, QLabel, QFrame, QApplication,
                            QInputDialog, QGroupBox, QListWidget, QListWidgetItem)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer, QMimeData
from PyQt6.QtGui import QAction, QPalette, QColor, QDrag, QIcon

from ..core.models import ActionDefinition, ActionType, SequenceItem, SequenceItemStatus
from ..widgets import ActionListWidget, SequenceListWidget, ControlPanel, LogWidget
from ..widgets.ai_assistant import AIAssistantWidget
from .dialogs import ActionConfigDialog
from ..core.storage import StorageManager
from .execution import ExecutionThread
from ..core.config_loader import Config


class TaskLibraryListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(24, 24))
        self.setSpacing(2)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)

    def startDrag(self, supportedActions):
        current_item = self.currentItem()
        if current_item is None:
            return

        task_name = current_item.data(Qt.ItemDataRole.UserRole)
        if not task_name:
            return

        mime = QMimeData()
        mime.setData("application/x-task-name", task_name.encode("utf-8"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(current_item.icon().pixmap(50, 50))
        drag.exec(Qt.DropAction.CopyAction)


class TaskComposerListWidget(QListWidget):
    order_changed = pyqtSignal()
    task_dropped = pyqtSignal(str, int)
    action_dropped = pyqtSignal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setSpacing(12)
        self.setIconSize(QSize(120, 80))
        self.setStyleSheet("""
            QListWidget {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            QListWidget::item {
                border: 2px solid #ddd;
                border-radius: 8px;
                padding: 2px;
                font-size: 11px;
                font-weight: bold;
            }
            QListWidget::item:selected {
                border: 2px solid #2196F3;
            }
        """)

    def startDrag(self, supportedActions):
        current_item = self.currentItem()
        if current_item is None:
            return

        entry = current_item.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return

        payload = {
            "row": self.currentRow(),
        }
        mime = QMimeData()
        mime.setData("application/x-task-composer-item", json.dumps(payload).encode("utf-8"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(current_item.icon().pixmap(80, 54))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if (
            event.mimeData().hasFormat("application/x-task-name")
            or event.mimeData().hasFormat("application/x-action")
            or event.mimeData().hasFormat("application/x-task-composer-item")
        ):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if (
            event.mimeData().hasFormat("application/x-task-name")
            or event.mimeData().hasFormat("application/x-action")
            or event.mimeData().hasFormat("application/x-task-composer-item")
        ):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        insert_row = self._drop_row(event)

        if event.mimeData().hasFormat("application/x-task-composer-item"):
            payload = json.loads(bytes(event.mimeData().data("application/x-task-composer-item")).decode("utf-8"))
            source_row = payload["row"]
            if 0 <= source_row < self.count():
                item = self.takeItem(source_row)
                if source_row < insert_row:
                    insert_row -= 1
                self.insertItem(insert_row, item)
                self.setCurrentRow(insert_row)
                self.order_changed.emit()
                event.accept()
            return

        if event.mimeData().hasFormat("application/x-task-name"):
            task_name = bytes(event.mimeData().data("application/x-task-name")).decode("utf-8")
            self.task_dropped.emit(task_name, insert_row)
            event.accept()
            return

        if event.mimeData().hasFormat("application/x-action"):
            data = event.mimeData().data("application/x-action")
            action_dict = json.loads(bytes(data).decode("utf-8"))
            action = ActionDefinition.from_dict(action_dict)
            self.action_dropped.emit(action, insert_row)
            event.accept()
            return

        event.ignore()

    def _drop_row(self, event) -> int:
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(position)
        if item is None:
            return self.count()
        return self.row(item)
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
            ActionType.VISION_CAPTURE: [],
            ActionType.TRAJECTORY: []
        }
        self.execution_thread: ExecutionThread = None
        self.is_paused = False
        self.config = Config.get_instance()
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
            self.initialize_robots()

        # 初始化底盘移动控制器
        self.initialize_move_controller()
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

        self.pose_panel = self.create_pose_panel()
        layout.addWidget(self.pose_panel)

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
        self.trajectory_list = ActionListWidget()

        self.action_tabs.addTab(self.move_list, "移动类")
        self.action_tabs.addTab(self.manipulate_list, "执行类")
        self.action_tabs.addTab(self.inspect_list, "检测类")
        self.action_tabs.addTab(self.change_gun_list, "换枪类")
        self.action_tabs.addTab(self.vision_capture_list, "视觉类")

        # AI助手 Tab
        self.ai_assistant_widget = AIAssistantWidget()
        self.action_tabs.addTab(self.ai_assistant_widget, "🤖 AI助手")

        self.action_tabs.addTab(self.trajectory_list, "Trajectory")

        layout.addWidget(self.action_tabs, stretch=2)

        task_library_group = QGroupBox("Saved Tasks")
        task_library_layout = QVBoxLayout(task_library_group)
        task_library_layout.setContentsMargins(6, 6, 6, 6)
        task_library_layout.setSpacing(4)
        self.task_library_list = TaskLibraryListWidget()
        self.task_library_list.setMinimumHeight(140)
        self.task_library_list.itemDoubleClicked.connect(lambda _: self.add_task_to_composer())
        task_library_layout.addWidget(self.task_library_list)
        layout.addWidget(task_library_group, stretch=1)
        self.refresh_task_library()

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
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.workflow_tabs = QTabWidget()
        self.workflow_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.workflow_tabs.setMovable(False)

        action_page = QWidget()
        action_layout = QVBoxLayout(action_page)
        action_layout.setContentsMargins(2, 2, 2, 2)
        action_layout.setSpacing(2)

        self.sequence_list = SequenceListWidget()
        self.sequence_list.setMinimumHeight(140)
        action_layout.addWidget(self.sequence_list, stretch=2)

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
        action_layout.addWidget(self.control_panel)

        task_page = QWidget()
        task_layout = QVBoxLayout(task_page)
        task_layout.setContentsMargins(2, 2, 2, 2)
        task_layout.setSpacing(2)
        self.task_composer_panel = self.create_task_composer_panel()
        task_layout.addWidget(self.task_composer_panel, stretch=1)

        self.workflow_tabs.addTab(action_page, "动作编排")
        self.workflow_tabs.addTab(task_page, "Task 组合")
        layout.addWidget(self.workflow_tabs, stretch=1)

        self.basic_control_panel = self.create_basic_control_panel()
        layout.addWidget(self.basic_control_panel)

        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        return panel
    def create_task_composer_panel(self) -> QWidget:
        panel = QGroupBox("Task Composer")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        composer_layout = QVBoxLayout()
        composer_title = QLabel("Combined Plan")
        composer_title.setStyleSheet("font-size: 12px; font-weight: bold;")
        self.task_composer_list = TaskComposerListWidget()
        self.task_composer_list.setMinimumHeight(140)
        self.task_composer_list.task_dropped.connect(self._add_task_name_to_composer)
        self.task_composer_list.action_dropped.connect(self._add_action_to_composer)
        self.task_composer_list.order_changed.connect(self._refresh_task_composer_display)
        composer_layout.addWidget(composer_title)
        composer_layout.addWidget(self.task_composer_list)
        layout.addLayout(composer_layout, stretch=1)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(4)
        self.refresh_tasks_btn = QPushButton("Refresh")
        self.refresh_tasks_btn.setMinimumHeight(26)
        self.refresh_tasks_btn.clicked.connect(self.refresh_task_library)
        self.add_task_btn = QPushButton("Add")
        self.add_task_btn.setMinimumHeight(26)
        self.add_task_btn.clicked.connect(self.add_task_to_composer)
        self.remove_task_btn = QPushButton("Remove")
        self.remove_task_btn.setMinimumHeight(26)
        self.remove_task_btn.clicked.connect(self.remove_task_from_composer)
        self.task_up_btn = QPushButton("Up")
        self.task_up_btn.setMinimumHeight(26)
        self.task_up_btn.clicked.connect(self.move_composed_task_up)
        self.task_down_btn = QPushButton("Down")
        self.task_down_btn.setMinimumHeight(26)
        self.task_down_btn.clicked.connect(self.move_composed_task_down)
        edit_row.addWidget(self.refresh_tasks_btn)
        edit_row.addWidget(self.add_task_btn)
        edit_row.addWidget(self.remove_task_btn)
        edit_row.addWidget(self.task_up_btn)
        edit_row.addWidget(self.task_down_btn)
        layout.addLayout(edit_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        self.execute_composed_task_btn = QPushButton("执行当前组合")
        self.execute_composed_task_btn.setMinimumHeight(28)
        self.execute_composed_task_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.execute_composed_task_btn.clicked.connect(self.execute_composed_task)
        self.save_combined_task_btn = QPushButton("Save Combined")
        self.save_combined_task_btn.setMinimumHeight(28)
        self.save_combined_task_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        self.save_combined_task_btn.clicked.connect(self.save_composed_task)
        self.clear_composer_btn = QPushButton("Clear")
        self.clear_composer_btn.setMinimumHeight(28)
        self.clear_composer_btn.clicked.connect(self.clear_task_composer)
        action_row.addWidget(self.execute_composed_task_btn)
        action_row.addWidget(self.save_combined_task_btn)
        action_row.addWidget(self.clear_composer_btn)
        layout.addLayout(action_row)

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

    def record_trajectory(self, robot_name: str):
        robot = self._get_trajectory_robot(robot_name)
        if robot is None:
            QMessageBox.warning(self, "Warning", f"{robot_name.upper()} is not connected")
            return

        default_path = self._next_trajectory_file(robot_name)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {robot_name.upper()} trajectory",
            str(default_path),
            "Trajectory Files (*.txt);;All Files (*)"
        )
        if not filename:
            return

        try:
            self.log_widget.append_log(f"{robot_name.upper()} starting drag teach")
            result = robot.rm_start_drag_teach(1)
            if result != 0:
                QMessageBox.warning(self, "Warning", f"Failed to start drag teach, code: {result}")
                self.log_widget.append_log(f"{robot_name.upper()} drag teach start failed: {result}")
                return

            QMessageBox.information(
                self,
                "Trajectory Recording",
                f"{robot_name.upper()} is recording now. Move the arm by hand, then click OK to stop and save."
            )

            stop_result = robot.rm_stop_drag_teach()
            if stop_result != 0:
                QMessageBox.warning(self, "Warning", f"Failed to stop drag teach, code: {stop_result}")
                self.log_widget.append_log(f"{robot_name.upper()} drag teach stop failed: {stop_result}")
                return

            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            save_result = robot.rm_save_trajectory(filename)
            if save_result[0] == 0:
                self.log_widget.append_log(
                    f"{robot_name.upper()} trajectory saved: {filename}, points: {save_result[1]}"
                )
                QMessageBox.information(self, "Trajectory Saved", f"Saved to:\n{filename}")
                return filename
            else:
                QMessageBox.warning(self, "Warning", f"Failed to save trajectory, code: {save_result[0]}")
                self.log_widget.append_log(f"{robot_name.upper()} trajectory save failed: {save_result[0]}")
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Trajectory record error: {e}")
            self.log_widget.append_log(f"{robot_name.upper()} trajectory record error: {e}")
        return None

    def run_trajectory(self, robot_name: str):
        robot = self._get_trajectory_robot(robot_name)
        if robot is None:
            QMessageBox.warning(self, "Warning", f"{robot_name.upper()} is not connected")
            return

        start_dir = self._trajectory_dir(robot_name)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {robot_name.upper()} trajectory",
            str(start_dir),
            "Trajectory Files (*.txt);;All Files (*)"
        )
        if not filename:
            return

        action = ActionDefinition(
            id=str(uuid4()),
            name=f"{robot_name.upper()} {Path(filename).stem}",
            type=ActionType.TRAJECTORY,
            parameters={
                "robot": robot_name,
                "file_path": filename,
            },
        )
        self._start_sequence_execution([SequenceItem.from_definition(action)], display_list=None, label="轨迹")

    def on_trajectory_succeeded(self, message: str):
        self.log_widget.append_log(message)
        QMessageBox.information(self, "Trajectory", message)

    def on_trajectory_failed(self, message: str):
        self.log_widget.append_log(message)
        QMessageBox.warning(self, "Trajectory", message)

    def _get_trajectory_robot(self, robot_name: str):
        if self.robot_controller is None:
            return None

        ctrl_name = "robot1_ctrl" if robot_name == "robot1" else "robot2_ctrl"
        ctrl = getattr(self.robot_controller, ctrl_name, None)
        return getattr(ctrl, "robot", None)

    def _trajectory_dir(self, robot_name: str) -> Path:
        return Path(__file__).resolve().parents[1] / "actions" / "Path" / robot_name

    def _next_trajectory_file(self, robot_name: str) -> Path:
        trajectory_dir = self._trajectory_dir(robot_name)
        trajectory_dir.mkdir(parents=True, exist_ok=True)

        existing_numbers = []
        for path in trajectory_dir.glob("trajectory_*.txt"):
            number_text = path.stem.rsplit("_", 1)[-1]
            if number_text.isdigit():
                existing_numbers.append(int(number_text))

        next_number = max(existing_numbers, default=0) + 1
        return trajectory_dir / f"trajectory_{next_number:03d}.txt"

    def _set_trajectory_buttons_enabled(self, enabled: bool):
        self.update_basic_control_buttons()
        if not enabled:
            for attr in (
                "record_robot1_path_btn",
                "run_robot1_path_btn",
                "record_robot2_path_btn",
                "run_robot2_path_btn",
            ):
                if hasattr(self, attr):
                    getattr(self, attr).setEnabled(False)

    def _pause_pose_refresh(self):
        if self.pose_timer is not None and self.pose_timer.isActive():
            self.pose_timer.stop()

    def _resume_pose_refresh(self):
        if self.pose_timer is not None and not self.pose_timer.isActive():
            self.pose_timer.start()

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

        lock = getattr(ctrl, "sdk_lock", None)
        lock_acquired = False
        try:
            if lock is not None:
                lock_acquired = lock.acquire(blocking=False)
                if not lock_acquired:
                    return self.robot_pose_cache.get(robot_name)
            ret, state = robot.rm_get_current_arm_state()
            if ret != 0:
                return None
            pose = state.get("pose")
            if not isinstance(pose, (list, tuple)) or len(pose) < 6:
                return None
            return [float(v) for v in pose[:6]]
        except Exception:
            return None
        finally:
            if lock is not None and lock_acquired:
                lock.release()

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

    def initialize_robots(self):
        """初始化机械臂"""
        if not ROBOT_AVAILABLE:
            self.log_widget.append_log("机械臂模块不可用")
            return

        self.log_widget.append_log("开始初始化机械臂...")

        try:
            # 创建机械臂控制器
            self.robot_controller = RobotController()

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

    def initialize_move_controller(self) -> None:
        """初始化底盘移动控制器"""
        try:
            from ..base_move.move_controller import RobotMoveController
            self.log_widget.append_log("初始化底盘移动控制器...")
            self.move_controller = RobotMoveController()
            self.move_controller.connect()
            self.log_widget.append_log("底盘移动控制器初始化成功")
        except Exception as e:
            self.log_widget.append_log(f"底盘移动控制器初始化失败：{e}")

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
            success = YIYEQIANG_INIT(port=self.config.KUAIHUANSHOU_SERIAL_PORT)
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
            success = YIYEQIANG_INIT(port=self.config.KUAIHUANSHOU_SERIAL_PORT)
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
            success = YIYEQIANG_EJECT(port=self.config.KUAIHUANSHOU_SERIAL_PORT)
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
            self.body_controller = ModbusMotor(port=self.config.BODY_SERIAL_PORT, baudrate=115200, slave_id=1, timeout=1)
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

        if action_type == ActionType.TRAJECTORY:
            self.create_trajectory_action()
            return

        dialog = ActionConfigDialog(action_type)
        if dialog.exec():
            action = dialog.get_action_definition()
            self.actions[action.type].append(action)
            self.refresh_action_list(action.type)
            self.save_actions()

    def create_trajectory_action(self):
        options = ["Record R1", "Record R2", "Use Existing File"]
        selected, ok = QInputDialog.getItem(
            self,
            "Trajectory Action",
            "Create trajectory action:",
            options,
            0,
            False
        )
        if not ok:
            return

        if selected == "Record R1":
            robot_name = "robot1"
            file_path = self.record_trajectory(robot_name)
        elif selected == "Record R2":
            robot_name = "robot2"
            file_path = self.record_trajectory(robot_name)
        else:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select trajectory file",
                str(self._trajectory_dir("robot1")),
                "Trajectory Files (*.txt);;All Files (*)"
            )
            if not file_path:
                return
            robot_options = ["R1", "R2"]
            robot_selected, robot_ok = QInputDialog.getItem(
                self,
                "Trajectory Robot",
                "Run trajectory on:",
                robot_options,
                0,
                False
            )
            if not robot_ok:
                return
            robot_name = "robot2" if robot_selected == "R2" else "robot1"

        if not file_path:
            return

        default_name = f"{robot_name.upper()} {Path(file_path).stem}"
        name, name_ok = QInputDialog.getText(
            self,
            "Trajectory Action Name",
            "Action name:",
            text=default_name
        )
        if not name_ok:
            return

        name = name.strip() or default_name
        from uuid import uuid4
        action = ActionDefinition(
            id=str(uuid4()),
            name=name,
            type=ActionType.TRAJECTORY,
            parameters={
                "robot": robot_name,
                "file_path": file_path
            }
        )
        self.actions[ActionType.TRAJECTORY].append(action)
        self.refresh_action_list(ActionType.TRAJECTORY)
        self.save_actions()
        self.log_widget.append_log(f"Trajectory action created: {name}")

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
            4: ActionType.VISION_CAPTURE,
            6: ActionType.TRAJECTORY
        }
        action_type = action_type_map.get(current_tab)
        if action_type is None:
            return

        list_map = {
            ActionType.MANIPULATE: self.manipulate_list,
            ActionType.INSPECT: self.inspect_list,
            ActionType.CHANGE_GUN: self.change_gun_list,
            ActionType.VISION_CAPTURE: self.vision_capture_list,
            ActionType.TRAJECTORY: self.trajectory_list
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
            ActionType.VISION_CAPTURE: self.vision_capture_list,
            ActionType.TRAJECTORY: self.trajectory_list
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
            4: ActionType.VISION_CAPTURE,
            6: ActionType.TRAJECTORY
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
            4: self.vision_capture_list,
            6: self.trajectory_list
        }
        return tab_list_map.get(current_tab)

    def refresh_task_library(self):
        if not hasattr(self, "task_library_list"):
            return

        self.task_library_list.clear()
        for task_name in sorted(StorageManager.list_tasks()):
            step_count = len(StorageManager.load_sequence(task_name))
            item = QListWidgetItem(f"{task_name} ({step_count} steps)")
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item.setSizeHint(QSize(100, 36))
            item.setIcon(self._create_task_list_icon())
            item.setToolTip(f"{task_name}\nSteps: {step_count}\nDrag to Combined Plan")
            item.setData(Qt.ItemDataRole.UserRole, task_name)
            self.task_library_list.addItem(item)

    def _create_task_list_icon(self) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter

        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor(76, 132, 180))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 20, 20, 4, 4)
        painter.end()
        return QIcon(pixmap)

    def add_task_to_composer(self):
        current_item = self.task_library_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "Warning", "Please select a saved task first")
            return

        task_name = current_item.data(Qt.ItemDataRole.UserRole)
        self._add_task_name_to_composer(task_name, self.task_composer_list.count())

    def _add_task_name_to_composer(self, task_name: str, insert_row: int | None = None):
        step_count = len(StorageManager.load_sequence(task_name))
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {"kind": "task", "task_name": task_name})
        if insert_row is None or insert_row >= self.task_composer_list.count():
            self.task_composer_list.addItem(item)
        else:
            self.task_composer_list.insertItem(max(0, insert_row), item)
        self._refresh_task_composer_display()

        if hasattr(self, "log_widget"):
            self.log_widget.append_log(f"Added task to composer: {task_name} ({step_count} steps)")

    def _add_action_to_composer(self, action: ActionDefinition, insert_row: int | None = None):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {"kind": "action", "action": action})
        if insert_row is None or insert_row >= self.task_composer_list.count():
            self.task_composer_list.addItem(item)
        else:
            self.task_composer_list.insertItem(max(0, insert_row), item)
        self._refresh_task_composer_display()

        if hasattr(self, "log_widget"):
            self.log_widget.append_log(f"Added action to composer: {action.name}")

    def remove_task_from_composer(self):
        row = self.task_composer_list.currentRow()
        if row >= 0:
            self.task_composer_list.takeItem(row)
            self._refresh_task_composer_display()

    def move_composed_task_up(self):
        self._move_composed_task(-1)

    def move_composed_task_down(self):
        self._move_composed_task(1)

    def _move_composed_task(self, offset: int):
        current_row = self.task_composer_list.currentRow()
        target_row = current_row + offset
        if current_row < 0 or target_row < 0 or target_row >= self.task_composer_list.count():
            return

        item = self.task_composer_list.takeItem(current_row)
        self.task_composer_list.insertItem(target_row, item)
        self.task_composer_list.setCurrentRow(target_row)
        self._refresh_task_composer_display()

    def clear_task_composer(self):
        self.task_composer_list.clear()

    def expand_composed_tasks(self, replace: bool):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            QMessageBox.warning(self, "Warning", "Please add at least one task to the composer")
            return

        if replace:
            self.sequence_list.clear_sequence()

        for item in sequence:
            self.sequence_list.add_sequence_item(item)

        mode = "replaced" if replace else "appended"
        self.log_widget.append_log(f"Task composer {mode} sequence with {len(sequence)} actions")

    def save_composed_task(self):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            QMessageBox.warning(self, "Warning", "Please add at least one task to the composer")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Combined Task", "", "Task Files (*.task)"
        )
        if not filename:
            return

        task_name = Path(filename).name
        StorageManager.save_sequence(sequence, task_name)
        self.refresh_task_library()
        self.log_widget.append_log(f"Combined task saved: {task_name}")

    def _build_composed_task_sequence(self) -> list[SequenceItem]:
        sequence: list[SequenceItem] = []
        for row in range(self.task_composer_list.count()):
            list_item = self.task_composer_list.item(row)
            entry = list_item.data(Qt.ItemDataRole.UserRole)
            if entry.get("kind") == "action":
                cloned_item = SequenceItem(
                    uuid=str(uuid4()),
                    definition=entry["action"],
                    status=SequenceItemStatus.PENDING,
                )
                sequence.append(cloned_item)
                continue

            task_name = entry.get("task_name", "")
            for task_item in StorageManager.load_sequence(task_name):
                cloned_item = SequenceItem(
                    uuid=str(uuid4()),
                    definition=task_item.definition,
                    status=SequenceItemStatus.PENDING,
                )
                sequence.append(cloned_item)
        return sequence

    def _refresh_task_composer_display(self):
        for row in range(self.task_composer_list.count()):
            item = self.task_composer_list.item(row)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry.get("kind") == "action":
                action = entry["action"]
                item.setText(f"{action.name} (action)")
                item.setIcon(self._create_action_card_icon(action))
                item.setToolTip(f"{action.name}\nType: {action.type.value}\nDrag to reorder")
                continue

            task_name = entry.get("task_name", "")
            step_count = len(StorageManager.load_sequence(task_name))
            item.setText(f"{task_name} ({step_count} steps)")
            item.setIcon(self._create_task_card_icon(task_name, step_count, task_name))
            item.setToolTip(f"{task_name}\nSteps: {step_count}\nDrag to reorder")

    def _create_task_card_icon(self, task_name: str, step_count: int, title: str | None = None):
        from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor
        from PyQt6.QtCore import QRectF

        width, height = 120, 80
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(76, 132, 180) if title is None else QColor(96, 125, 139))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, width - 8, height - 8, 8, 8)

        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)
        font.setPointSize(16)
        painter.setFont(font)
        header_source = title[:-5] if title and title.endswith(".task") else (title or "TASK")
        header = header_source[:12] + ".." if len(header_source) > 12 else header_source
        painter.drawText(QRectF(6, 6, width - 12, 24), Qt.AlignmentFlag.AlignLeft, header)

        font.setPointSize(10)
        font.setBold(False)
        painter.setFont(font)
        display_name = task_name[:-5] if task_name.endswith(".task") else task_name
        truncated_name = display_name[:10] + ".." if len(display_name) > 10 else display_name
        painter.drawText(
            QRectF(6, 34, width - 12, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            truncated_name,
        )

        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, height - 22, width, 18),
            Qt.AlignmentFlag.AlignCenter,
            f"{step_count} steps",
        )
        painter.end()
        return QIcon(pixmap)

    def _create_action_card_icon(self, action: ActionDefinition):
        from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor
        from PyQt6.QtCore import QRectF

        width, height = 120, 80
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        colors = {
            ActionType.MOVE: QColor(100, 149, 237),
            ActionType.BASE_MOVE: QColor(255, 99, 71),
            ActionType.MANIPULATE: QColor(255, 140, 0),
            ActionType.WAIT: QColor(255, 140, 0),
            ActionType.INSPECT: QColor(60, 179, 113),
            ActionType.CHANGE_GUN: QColor(147, 112, 219),
            ActionType.VISION_CAPTURE: QColor(30, 144, 255),
            ActionType.TRAJECTORY: QColor(0, 150, 136),
        }

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(colors.get(action.type, QColor(128, 128, 128)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, width - 8, height - 8, 8, 8)

        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)
        font.setPointSize(16)
        painter.setFont(font)
        header = action.name[:12] + ".." if len(action.name) > 12 else action.name
        painter.drawText(QRectF(6, 6, width - 12, 24), Qt.AlignmentFlag.AlignLeft, header)

        font.setPointSize(10)
        font.setBold(False)
        painter.setFont(font)
        truncated_name = action.name[:10] + ".." if len(action.name) > 10 else action.name
        painter.drawText(
            QRectF(6, 34, width - 12, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            truncated_name,
        )

        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, height - 22, width, 18),
            Qt.AlignmentFlag.AlignCenter,
            "action",
        )
        painter.end()
        return QIcon(pixmap)

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
            self.refresh_task_library()
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
            self.refresh_task_library()
            self.log_widget.append_log(f"任务已加载: {task_name}")

    def start_execution(self):
        sequence = self.sequence_list.get_sequence()
        if not sequence:
            QMessageBox.warning(self, "警告", "请先添加动作到序列中")
            return

        self._start_sequence_execution(sequence, display_list=self.sequence_list, label="动作编排序列")

    def execute_composed_task(self):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            QMessageBox.warning(self, "Warning", "Please add at least one task or action to the composer")
            return

        self._start_sequence_execution(sequence, display_list=None, label="Task 组合序列")

    def _start_sequence_execution(self, sequence: list[SequenceItem], display_list=None, label: str = "序列"):
        if self.execution_thread and self.execution_thread.isRunning():
            QMessageBox.warning(self, "Warning", "A sequence is already running")
            return

        self.log_widget.append_log(f"开始执行{label}...")
        self._execution_display_list = display_list
        self._set_trajectory_buttons_enabled(False)
        self._pause_pose_refresh()

        for item in sequence:
            item.status = SequenceItemStatus.PENDING
      
        if display_list is not None:
            for i, item in enumerate(sequence):
                display_list.update_item_status(i, item)

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
        display_list = getattr(self, "_execution_display_list", self.sequence_list)
        if display_list is not None:
            display_list.update_item_status(index, item)
            list_item = display_list.item(index)
            if list_item is not None:
                display_list.scrollToItem(list_item)

    def on_step_completed(self, index: int, item: SequenceItem):
        display_list = getattr(self, "_execution_display_list", self.sequence_list)
        if display_list is not None:
            display_list.update_item_status(index, item)

    def on_step_failed(self, index: int, item: SequenceItem, error_msg: str):
        display_list = getattr(self, "_execution_display_list", self.sequence_list)
        if display_list is not None:
            display_list.update_item_status(index, item)
        QMessageBox.critical(self, "执行失败", f"步骤 {index + 1} 失败:\n{error_msg}")

    def on_execution_finished(self):
        self.log_widget.append_log("序列执行完成")
        self.is_paused = False
        self.control_panel.pause_btn.setText("暂停")
        self._execution_display_list = self.sequence_list
        self._set_trajectory_buttons_enabled(True)
        self._resume_pose_refresh()
        self.refresh_arm_poses()

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
