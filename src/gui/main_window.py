import json
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import List
from uuid import uuid4

from PyQt6.QtCore import QMimeData, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDrag, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai_integration.execution_bridge import ExecutionBridge
from ..application import (
    ApplicationServices,
    CompositionChangeType,
    CompositionEvent,
    CompositionRevisionConflict,
)
from ..core.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ..device_runtime import StopMode
from ..device_runtime.ids import (
    BODY_AXIS,
    MOBILE_BASE,
    RELAY_BANK,
    ROBOT_SYSTEM,
)
from ..widgets import ActionListWidget, ControlPanel, LogWidget, SequenceListWidget
from ..widgets.ai_assistant import AIAssistantWidget
from .composition_bridge import CompositionBridge
from .dialogs import ActionConfigDialog
from .startup import GuiStartupLifecycle, GuiStartupState


class TaskLibraryListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(28, 28))
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
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setSpacing(12)
        self.setIconSize(QSize(130, 88))
        self.setStyleSheet("""
            QListWidget {
                background-color: #f8fafc;
                border: 2px dashed #cbd5e1;
                border-radius: 12px;
                padding: 4px;
            }
            QListWidget::item {
                border: 2px solid transparent;
                border-radius: 10px;
                padding: 1px;
                font-size: 11px;
                font-weight: bold;
                background: transparent;
            }
            QListWidget::item:hover {
                border-color: #93c5fd;
                background: rgba(59, 130, 246, 0.06);
            }
            QListWidget::item:selected {
                border: 2px solid #3b82f6;
                background: rgba(59, 130, 246, 0.10);
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
class MainWindow(QMainWindow):
    def __init__(self, services: ApplicationServices):
        super().__init__()
        self._services = services
        self._execution_bridge = ExecutionBridge(services)
        self.actions: dict[ActionType, list[ActionDefinition]] = {
            ActionType.MOVE: [],
            ActionType.BASE_MOVE: [],
            ActionType.MANIPULATE: [],
            ActionType.INSPECT: [],
            ActionType.WAIT: [],
            ActionType.CHANGE_GUN: [],
            ActionType.VISION_CAPTURE: [],
            ActionType.VISION_RELOCALIZE: [],
            ActionType.TRAJECTORY: []
        }
        self.is_paused = False
        self.settings = services.settings
        self.robot1_connected = False
        self.robot2_connected = False

        self.body_connected = False
        self.robot_pose_cache = {"robot1": None, "robot2": None}
        self.pose_timer = None
        self._startup_lifecycle = GuiStartupLifecycle()
        self._speech_startup_wait_timer = None

        self.init_ui()
        self._sequence_revision = (
            services.composition.sequence_revision
        )
        self._composition_bridge = CompositionBridge(
            services.composition,
            self,
        )
        self._composition_bridge.changed.connect(
            self._on_composition_changed,
            Qt.ConnectionType.QueuedConnection,
        )
        self.sequence_list.sequence_changed.connect(
            self._publish_current_sequence
        )
        self.load_actions()
        self._render_sequence(
            self._services.composition.sequence_entries()
        )
        self._execution_bridge.step_started.connect(self.on_step_started)
        self._execution_bridge.step_completed.connect(self.on_step_completed)
        self._execution_bridge.step_failed.connect(self.on_step_failed)
        self._execution_bridge.log_message.connect(self.log_widget.append_log)
        self._execution_bridge.loop_progress.connect(self.on_loop_progress)
        self._execution_bridge.execution_completed.connect(
            self.on_execution_completed
        )

        # 设置 AI助手的主窗口引用（用于执行桥接器）
        if hasattr(self, 'ai_assistant_widget'):
            self.ai_assistant_widget.set_main_window(self)
            self.ai_assistant_widget.speech_runtime_startup_finished.connect(
                self.initialize_startup_hardware
            )
        self.start_startup_initialization()

    @property
    def robot_system(self):
        """Return the runtime-owned robot system for read/teach diagnostics."""
        return self._services.device_runtime.get_if_ready(ROBOT_SYSTEM)

    @property
    def startup_state(self) -> GuiStartupState:
        return self._startup_lifecycle.state

    def start_startup_initialization(self):
        """启动 GUI 显示前的必要初始化流程。"""
        if not self._startup_lifecycle.begin():
            return

        try:
            speech_start_requested = False
            if hasattr(self, 'ai_assistant_widget'):
                speech_start_requested = (
                    self.ai_assistant_widget.start_voice_speech_runtime_if_configured()
                )
        except Exception:
            self._startup_lifecycle.mark_failed()
            raise

        if not speech_start_requested:
            self.initialize_startup_hardware()
            return
        if self.startup_state is not GuiStartupState.WAITING_FOR_SPEECH:
            return

        timeout_s = self.settings.voice.voice_speech_startup_wait_timeout_s
        if timeout_s <= 0:
            self._startup_lifecycle.mark_failed()
            raise ValueError("voice speech startup wait timeout must be positive")
        self._speech_startup_wait_timer = QTimer(self)
        self._speech_startup_wait_timer.setSingleShot(True)
        self._speech_startup_wait_timer.timeout.connect(
            self._on_speech_startup_wait_timeout
        )
        self._speech_startup_wait_timer.start(int(timeout_s * 1000))

    def initialize_startup_hardware(self, speech_ready: bool = False):
        """初始化启动阶段硬件；若启用 ASR/KWS，则在语音 runtime 之后执行。"""
        if not self._startup_lifecycle.begin_hardware_initialization():
            return
        if self._speech_startup_wait_timer is not None:
            self._speech_startup_wait_timer.stop()
            self._speech_startup_wait_timer = None

        try:
            self.initialize_robots()
            if self.settings.devices.body_di_pan:
                self.initialize_move_controller()
            self.initialize_body()
            self.initialize_pipette_on_startup()
        except Exception:
            self._startup_lifecycle.mark_failed()
            raise
        self._startup_lifecycle.mark_ready()

    def _on_speech_startup_wait_timeout(self):
        """Continue hardware startup if ASR/KWS first-load is still downloading."""
        if self.startup_state is not GuiStartupState.WAITING_FOR_SPEECH:
            return
        if hasattr(self, "ai_assistant_widget"):
            self.ai_assistant_widget.notify_speech_startup_wait_timeout()
        self.initialize_startup_hardware(False)

    def init_ui(self):
        self.setWindowTitle("机器人动作编排器")
        self.setMinimumSize(540, 800)
        self.resize(540, 960)

        # ── 全局 Modern Light 样式表 ──
        self.setStyleSheet(self._make_global_stylesheet())

        self.create_menu()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

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

    @staticmethod
    def _make_global_stylesheet() -> str:
        return """
        /* ═══════════════════════════════════════════════════
           Global Design System — Light Modern Theme
           ═══════════════════════════════════════════════════ */
        QMainWindow { background: #f1f5f9; }
        QWidget { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 13px; color: #1e293b; }

        /* ── Buttons ── */
        QPushButton {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 5px 12px;
            color: #1e293b;
            font-weight: 500;
            min-height: 26px;
        }
        QPushButton:hover  { background: #f8fafc; border-color: #94a3b8; }
        QPushButton:pressed { background: #f1f5f9; }
        QPushButton:disabled { background: #f8fafc; color: #94a3b8; border-color: #e2e8f0; }

        /* ── Tabs ── */
        QTabWidget::pane {
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            background: #ffffff;
            top: -1px;
        }
        QTabBar::tab {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-bottom: none;
            border-radius: 6px 6px 0 0;
            padding: 6px 14px;
            color: #64748b;
            font-weight: 500;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #3b82f6;
            font-weight: 700;
            border-bottom: 2px solid #3b82f6;
        }
        QTabBar::tab:hover:!selected { background: #eff6ff; color: #3b82f6; }

        /* ── GroupBox ── */
        QGroupBox {
            font-weight: 700;
            color: #334155;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            margin-top: 14px;
            padding: 14px 8px 8px;
            background: #ffffff;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #3b82f6; }

        /* ── Inputs ── */
        QLineEdit, QSpinBox, QDoubleSpinBox {
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 5px 8px;
            background: #ffffff;
            color: #1e293b;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #3b82f6; }
        QComboBox {
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 5px 8px;
            background: #ffffff;
            color: #1e293b;
        }
        QComboBox:hover { border-color: #3b82f6; }
        QComboBox::drop-down { border: none; width: 24px; }
        QComboBox QAbstractItemView {
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            background: #ffffff;
            selection-background-color: #eff6ff;
            selection-color: #1e293b;
        }

        /* ── Lists ── */
        QListWidget {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            outline: none;
        }
        QListWidget::item { padding: 6px 10px; border-radius: 4px; }
        QListWidget::item:hover { background: #f8fafc; }
        QListWidget::item:selected { background: #eff6ff; color: #1e293b; border: 1px solid #bfdbfe; }

        /* ── Frames ── */
        QFrame[frameShape="6"] {
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            background: #ffffff;
        }

        /* ── CheckBox ── */
        QCheckBox { spacing: 6px; color: #334155; }
        QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #cbd5e1; background: #ffffff; }
        QCheckBox::indicator:checked { background: #3b82f6; border-color: #3b82f6; }

        /* ── ScrollBar ── */
        QScrollBar:vertical { width: 6px; background: transparent; }
        QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 3px; min-height: 20px; }
        QScrollBar::handle:vertical:hover { background: #94a3b8; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal { height: 6px; background: transparent; }
        QScrollBar::handle:horizontal { background: #cbd5e1; border-radius: 3px; min-width: 20px; }
        QScrollBar::handle:horizontal:hover { background: #94a3b8; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

        /* ── Splitter ── */
        QSplitter::handle { width: 3px; background: #e2e8f0; }
        QSplitter::handle:hover { background: #3b82f6; }

        /* ── Menu ── */
        QMenuBar { background: #ffffff; border-bottom: 1px solid #e2e8f0; padding: 2px; }
        QMenuBar::item { padding: 4px 10px; border-radius: 4px; }
        QMenuBar::item:selected { background: #eff6ff; }
        QMenu { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 4px; }
        QMenu::item { padding: 6px 28px 6px 12px; border-radius: 4px; }
        QMenu::item:selected { background: #eff6ff; color: #1e293b; }

        /* ── TextEdit ── */
        QTextEdit {
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            background: #ffffff;
            color: #1e293b;
            font-family: "Cascadia Code", "Consolas", "SF Mono", monospace;
            font-size: 12px;
        }

        /* ── Tooltips ── */
        QToolTip {
            background: #1e293b;
            color: #f1f5f9;
            border: none;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 12px;
        }
        """

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
        self.ai_assistant_widget = AIAssistantWidget(self._services)
        self.action_tabs.addTab(self.ai_assistant_widget, "🤖 AI助手")

        self.action_tabs.addTab(self.trajectory_list, "轨迹类")

        layout.addWidget(self.action_tabs, stretch=2)

        task_library_group = QGroupBox("已保存任务")
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

        self.test_camera_btn = QPushButton("📷 测试相机")
        self.test_camera_btn.setMinimumHeight(32)
        self.test_camera_btn.setStyleSheet("""
            QPushButton { background: #10b981; color: #fff; font-weight: 700; border: none; border-radius: 6px; padding: 6px 12px; }
            QPushButton:hover { background: #059669; }
        """)
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
        self.control_panel.quick_stop_clicked.connect(
            lambda: self.request_safety_stop(StopMode.QUICK)
        )
        self.control_panel.emergency_stop_clicked.connect(
            lambda: self.request_safety_stop(StopMode.EMERGENCY)
        )
        self.control_panel.move_up_clicked.connect(self.move_item_up)
        self.control_panel.move_down_clicked.connect(self.move_item_down)
        self.control_panel.edit_clicked.connect(self.edit_sequence_item)
        self.control_panel.repeat_clicked.connect(self.repeat_sequence_selection)
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
        self.workflow_tabs.addTab(task_page, "任务组合")
        layout.addWidget(self.workflow_tabs, stretch=1)

        self.basic_control_panel = self.create_basic_control_panel()
        layout.addWidget(self.basic_control_panel)

        self.log_widget = LogWidget()
        layout.addWidget(self.log_widget)

        return panel
    def create_task_composer_panel(self) -> QWidget:
        panel = QGroupBox("任务组合器")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        composer_layout = QVBoxLayout()
        composer_title = QLabel("组合计划")
        composer_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #334155;")
        self.task_composer_list = TaskComposerListWidget()
        self.task_composer_list.setMinimumHeight(140)
        self.task_composer_list.task_dropped.connect(self._add_task_name_to_composer)
        self.task_composer_list.action_dropped.connect(self._add_action_to_composer)
        self.task_composer_list.order_changed.connect(self._refresh_task_composer_display)
        composer_layout.addWidget(composer_title)
        composer_layout.addWidget(self.task_composer_list)
        layout.addLayout(composer_layout, stretch=1)

        # Row 1: 编辑操作 — 与动作编排的控制面板顺序一致
        edit_row = QHBoxLayout()
        edit_row.setSpacing(4)
        self.task_up_btn = QPushButton("↑ 上移")
        self.task_up_btn.setMinimumHeight(28)
        self.task_up_btn.clicked.connect(self.move_composed_task_up)
        self.task_down_btn = QPushButton("↓ 下移")
        self.task_down_btn.setMinimumHeight(28)
        self.task_down_btn.clicked.connect(self.move_composed_task_down)
        self.task_repeat_btn = QPushButton("🔁 循环")
        self.task_repeat_btn.setMinimumHeight(28)
        self.task_repeat_btn.clicked.connect(self.repeat_composer_selection)
        self.remove_task_btn = QPushButton("🗑 移除")
        self.remove_task_btn.setMinimumHeight(28)
        self.remove_task_btn.clicked.connect(self.remove_task_from_composer)
        self.clear_composer_btn = QPushButton("✕ 清空")
        self.clear_composer_btn.setMinimumHeight(28)
        self.clear_composer_btn.clicked.connect(self.clear_task_composer)
        edit_row.addWidget(self.task_up_btn)
        edit_row.addWidget(self.task_down_btn)
        edit_row.addWidget(self.task_repeat_btn)
        edit_row.addWidget(self.remove_task_btn)
        edit_row.addWidget(self.clear_composer_btn)
        layout.addLayout(edit_row)

        # Row 2: 文件操作 — 与保存/载入对应
        file_row = QHBoxLayout()
        file_row.setSpacing(4)
        self.refresh_tasks_btn = QPushButton("🔄 刷新")
        self.refresh_tasks_btn.setMinimumHeight(28)
        self.refresh_tasks_btn.clicked.connect(self.refresh_task_library)
        self.add_task_btn = QPushButton("＋ 添加")
        self.add_task_btn.setMinimumHeight(28)
        self.add_task_btn.clicked.connect(self.add_task_to_composer)
        file_row.addWidget(self.refresh_tasks_btn)
        file_row.addWidget(self.add_task_btn)
        layout.addLayout(file_row)

        # Row 3: 执行操作 — 与动作编排控制面板一致
        exec_row = QHBoxLayout()
        exec_row.setSpacing(4)
        self.execute_composed_task_btn = QPushButton("▶ 执行当前组合")
        self.execute_composed_task_btn.setMinimumHeight(32)
        self.execute_composed_task_btn.setStyleSheet("""
            QPushButton { background: #22c55e; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #16a34a; }
        """)
        self.execute_composed_task_btn.clicked.connect(self.execute_composed_task)
        self.pause_composed_task_btn = QPushButton("⏸ 暂停")
        self.pause_composed_task_btn.setMinimumHeight(32)
        self.pause_composed_task_btn.setStyleSheet("""
            QPushButton { background: #f59e0b; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #d97706; }
        """)
        self.pause_composed_task_btn.clicked.connect(self.toggle_pause)
        exec_row.addWidget(self.execute_composed_task_btn)
        exec_row.addWidget(self.pause_composed_task_btn)
        layout.addLayout(exec_row)

        # Row 4: 停止 + 保存
        stop_row = QHBoxLayout()
        stop_row.setSpacing(4)
        self.stop_composed_task_btn = QPushButton("⏹ 停止任务")
        self.stop_composed_task_btn.setMinimumHeight(32)
        self.stop_composed_task_btn.setAccessibleName("停止任务")
        self.stop_composed_task_btn.setToolTip(
            "请求当前任务在可中断点停止；不会触发设备硬件急停"
        )
        self.stop_composed_task_btn.setStyleSheet("""
            QPushButton { background: #ef4444; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #dc2626; }
        """)
        self.stop_composed_task_btn.clicked.connect(self.stop_execution)
        self.save_combined_task_btn = QPushButton("💾 保存组合")
        self.save_combined_task_btn.setMinimumHeight(32)
        self.save_combined_task_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: #fff; font-weight: 700; border: none; border-radius: 6px; }
            QPushButton:hover { background: #2563eb; }
        """)
        self.save_combined_task_btn.clicked.connect(self.save_composed_task)
        stop_row.addWidget(self.stop_composed_task_btn)
        stop_row.addWidget(self.save_combined_task_btn)
        layout.addLayout(stop_row)

        return panel

    def create_pose_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        title = QLabel("📍 机械臂位姿")
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #334155;")
        self.refresh_pose_btn = QPushButton("刷新")
        self.refresh_pose_btn.setFixedHeight(24)
        self.refresh_pose_btn.clicked.connect(self.refresh_arm_poses)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_pose_btn)
        layout.addLayout(header_layout)

        self.robot1_pose_value_label, self.copy_robot1_pose_btn = self._build_pose_row(layout, "R1")
        self.robot2_pose_value_label, self.copy_robot2_pose_btn = self._build_pose_row(layout, "R2")
        self.localization_pose_value_label = self._build_localization_row(layout)

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

        title = QLabel("🎮 基础控制")
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #334155;")
        layout.addWidget(title)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self.gripper_open_btn = QPushButton("🔓 夹爪打开")
        self.gripper_open_btn.setMinimumHeight(28)
        self.gripper_open_btn.setStyleSheet("""
            QPushButton { border: 1px solid #e2e8f0; border-radius: 6px; font-weight: 500; }
            QPushButton:hover { background: #f0fdf4; border-color: #22c55e; color: #16a34a; }
        """)
        self.gripper_open_btn.clicked.connect(self.on_gripper_open_clicked)

        self.gripper_close_btn = QPushButton("🔒 夹爪关闭")
        self.gripper_close_btn.setMinimumHeight(28)
        self.gripper_close_btn.setStyleSheet("""
            QPushButton { border: 1px solid #e2e8f0; border-radius: 6px; font-weight: 500; }
            QPushButton:hover { background: #fef2f2; border-color: #ef4444; color: #dc2626; }
        """)
        self.gripper_close_btn.clicked.connect(self.on_gripper_close_clicked)

        self.init_pipette_btn = QPushButton("💉 退枪头")
        self.init_pipette_btn.setMinimumHeight(28)
        self.init_pipette_btn.setStyleSheet("""
            QPushButton { border: 1px solid #e2e8f0; border-radius: 6px; font-weight: 500; }
            QPushButton:hover { background: #fffbeb; border-color: #f59e0b; color: #d97706; }
        """)
        self.init_pipette_btn.clicked.connect(self.eject_pipette_tip)

        btn_layout.addWidget(self.gripper_open_btn)
        btn_layout.addWidget(self.gripper_close_btn)
        btn_layout.addWidget(self.init_pipette_btn)
        layout.addLayout(btn_layout)

        relay_group = QGroupBox("继电器控制")
        relay_layout = QVBoxLayout(relay_group)
        relay_layout.setContentsMargins(8, 6, 8, 6)
        relay_layout.setSpacing(6)

        relay_btn_row = QHBoxLayout()
        relay_btn_row.setSpacing(6)
        self.relay_y1_on_btn = QPushButton("Y1 开")
        self.relay_y1_off_btn = QPushButton("Y1 关")
        self.relay_y2_on_btn = QPushButton("Y2 开")
        self.relay_y2_off_btn = QPushButton("Y2 关")
        for btn in (
            self.relay_y1_on_btn, self.relay_y1_off_btn,
            self.relay_y2_on_btn, self.relay_y2_off_btn,
        ):
            btn.setMinimumHeight(28)
            btn.setStyleSheet("""
                QPushButton { border: 1px solid #e2e8f0; border-radius: 6px; font-weight: 500; }
                QPushButton:hover { background: #f8fafc; }
            """)
            relay_btn_row.addWidget(btn)

        self.relay_y1_on_btn.clicked.connect(lambda: self._set_relay_state("Y1", True))
        self.relay_y1_off_btn.clicked.connect(lambda: self._set_relay_state("Y1", False))
        self.relay_y2_on_btn.clicked.connect(lambda: self._set_relay_state("Y2", True))
        self.relay_y2_off_btn.clicked.connect(lambda: self._set_relay_state("Y2", False))
        relay_layout.addLayout(relay_btn_row)
        layout.addWidget(relay_group)
        self.relay_group = relay_group

        self.update_basic_control_buttons()
        return panel

    def update_basic_control_buttons(self):
        gripper_ready = self.robot_system is not None and self.robot1_connected
        if hasattr(self, "gripper_open_btn"):
            self.gripper_open_btn.setEnabled(gripper_ready)
        if hasattr(self, "gripper_close_btn"):
            self.gripper_close_btn.setEnabled(gripper_ready)
        relay_ready = RELAY_BANK in self._services.device_runtime.registered_device_ids()
        for attr in ("relay_y1_on_btn", "relay_y1_off_btn", "relay_y2_on_btn", "relay_y2_off_btn"):
            if hasattr(self, attr):
                getattr(self, attr).setEnabled(relay_ready)

    def _set_relay_state(self, channel: str, turn_on: bool):
        action_text = "打开" if turn_on else "关闭"
        try:
            channel_number = {"Y1": 1, "Y2": 2}[channel]
            self._services.manual_control.set_relay(
                channel_number,
                turn_on,
            )
            self.log_widget.append_log(f"继电器 {channel} 已{action_text}")
        except Exception as e:
            self.log_widget.append_log(f"继电器 {channel} {action_text}失败: {e}")
            QMessageBox.warning(self, "警告", f"继电器 {channel} {action_text}失败:\n{e}")

    def on_gripper_open_clicked(self):
        if self.robot_system is None or not self.robot1_connected:
            QMessageBox.warning(self, "警告", "Robot1 未连接")
            return

        try:
            success = self._services.manual_control.set_gripper(
                "left",
                opened=True,
            )
            if success:
                self.log_widget.append_log("Robot1 夹爪已打开")
            else:
                QMessageBox.warning(self, "警告", "夹爪打开失败")
                self.log_widget.append_log("Robot1 夹爪打开失败")
        except Exception as e:
            QMessageBox.warning(self, "警告", f"夹爪打开异常: {e}")
            self.log_widget.append_log(f"Robot1 夹爪打开异常: {e}")

    def on_gripper_close_clicked(self):
        if self.robot_system is None or not self.robot1_connected:
            QMessageBox.warning(self, "警告", "Robot1 未连接")
            return

        try:
            success = self._services.manual_control.set_gripper(
                "left",
                opened=False,
            )
            if success:
                self.log_widget.append_log("Robot1 夹爪已关闭")
            else:
                QMessageBox.warning(self, "警告", "夹爪关闭失败")
                self.log_widget.append_log("Robot1 夹爪关闭失败")
        except Exception as e:
            QMessageBox.warning(self, "警告", f"夹爪关闭异常: {e}")
            self.log_widget.append_log(f"Robot1 夹爪关闭异常: {e}")

    def record_trajectory(self, robot_name: str):
        if self.robot_system is None:
            QMessageBox.warning(self, "警告", f"{robot_name.upper()} 未连接")
            return

        default_path = self._next_trajectory_file(robot_name)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            f"保存 {robot_name.upper()} 轨迹",
            str(default_path),
            "轨迹文件 (*.txt);;所有文件 (*)"
        )
        if not filename:
            return

        teaching_started = False
        try:
            self.log_widget.append_log(f"{robot_name.upper()} 开始拖动示教")
            self._services.trajectory_teaching.start(robot_name)
            teaching_started = True

            QMessageBox.information(
                self,
                "轨迹录制",
                f"{robot_name.upper()} 正在录制。请手动拖动机械臂，完成后点击确定停止并保存。"
            )

            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            save_result = self._services.trajectory_teaching.stop_and_save(
                filename
            )
            teaching_started = False
            self.log_widget.append_log(
                f"{robot_name.upper()} 轨迹已保存: "
                f"{save_result.path}, 点数: {save_result.point_count}"
            )
            QMessageBox.information(
                self,
                "轨迹已保存",
                f"保存到:\n{save_result.path}",
            )
            return str(save_result.path)
        except Exception as e:
            if teaching_started:
                try:
                    self._services.trajectory_teaching.cancel()
                except Exception as stop_error:
                    self.log_widget.append_log(
                        f"{robot_name.upper()} 停止拖动示教失败: "
                        f"{stop_error}"
                    )
            QMessageBox.warning(self, "警告", f"轨迹录制异常: {e}")
            self.log_widget.append_log(f"{robot_name.upper()} 轨迹录制异常: {e}")
        return None

    def run_trajectory(self, robot_name: str):
        if self.robot_system is None:
            QMessageBox.warning(self, "警告", f"{robot_name.upper()} 未连接")
            return

        start_dir = self._trajectory_dir(robot_name)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {robot_name.upper()} 轨迹",
            str(start_dir),
            "轨迹文件 (*.txt);;所有文件 (*)"
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
        QMessageBox.information(self, "轨迹", message)

    def on_trajectory_failed(self, message: str):
        self.log_widget.append_log(message)
        QMessageBox.warning(self, "轨迹", message)

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
        row_label.setStyleSheet("font-weight: 700; color: #334155;")

        pose_label = QLabel("--")
        pose_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        copy_btn = QPushButton("复制")
        copy_btn.setFixedHeight(24)
        copy_btn.clicked.connect(lambda _, name=robot_label.lower().replace("r", "robot"): self.copy_robot_pose(name))

        row.addWidget(row_label)
        row.addWidget(pose_label, stretch=1)
        row.addWidget(copy_btn)
        parent_layout.addLayout(row)

        return pose_label, copy_btn

    def _build_localization_row(self, parent_layout: QVBoxLayout):
        row = QHBoxLayout()
        row_label = QLabel("底盘:")
        row_label.setFixedWidth(36)
        row_label.setStyleSheet("font-weight: 700; color: #334155;")

        localization_label = QLabel("--")
        localization_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        localization_label.setStyleSheet("color: #64748b;")

        row.addWidget(row_label)
        row.addWidget(localization_label, stretch=1)
        parent_layout.addLayout(row)

        return localization_label

    def refresh_arm_poses(self):
        self._refresh_single_robot_pose("robot1")
        self._refresh_single_robot_pose("robot2")
        self.refresh_localization_position()

    def refresh_localization_position(self):
        if not hasattr(self, "localization_pose_value_label"):
            return

        try:
            receiver = self._services.localization
            position = receiver.latest(
                max_age=10.0,
                valid_only=False,
                wait_timeout=0.0,
            )
            if position is None:
                error = receiver.last_error
                self.localization_pose_value_label.setText(f"UDP -- ({error})" if error else "UDP --")
                return
        except Exception as exc:
            self.localization_pose_value_label.setText(f"UDP error: {exc}")
            return

        self.localization_pose_value_label.setText(self.format_localization_text(position))

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
        try:
            state = self._services.robot_query.try_read_state(robot_name)
            if state is None:
                return self.robot_pose_cache.get(robot_name)
            return state.pose.to_list()
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

    def format_localization_text(self, position: dict):
        age = max(0.0, time.time() - float(position.get("timestamp", 0.0)))
        tag_id = int(position.get("id", -99))
        if tag_id == -99:
            return f"Tag未检测 | age:{age:.1f}s"

        return (
            f"ID:{tag_id} | "
            f"X:{float(position.get('x', 0.0)):.2f}cm "
            f"Y:{float(position.get('y', 0.0)):.2f}cm "
            f"Angle:{float(position.get('angle', 0.0)):.2f}deg | "
            f"age:{age:.1f}s"
        )

    def copy_robot_pose(self, robot_name: str):
        pose = self.robot_pose_cache.get(robot_name)
        if pose is None:
            self._refresh_single_robot_pose(robot_name)
            pose = self.robot_pose_cache.get(robot_name)

        if pose is None:
            QMessageBox.warning(self, "警告", f"{robot_name.upper()} 位姿不可用")
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

        title = QLabel("🔌 设备状态")
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #334155;")
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
            indicator.setStyleSheet("background-color: #ef4444; border-radius: 8px;")
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
        body_widget, self.body_status_indicator, body_text = make_status_item("body: 未连接", "body")
        body_text.setObjectName("body_status_text")
        hand_widget, self.pipette_status_indicator, hand_text = make_status_item("hand: 未连接", "hand")
        hand_text.setObjectName("hand_status_text")

        # 存储文本标签引用，供 update_* 方法直接使用
        self.robot1_status_text = r1_text
        self.robot2_status_text = r2_text
        self.body_status_text = body_text
        self.hand_status_text = hand_text

        status_layout.addWidget(r1_widget)
        status_layout.addWidget(r2_widget)
        status_layout.addWidget(body_widget)
        status_layout.addWidget(hand_widget)
        status_layout.addStretch()

        layout.addLayout(status_layout)
        return bar

    def initialize_robots(self):
        """初始化机械臂"""
        self.log_widget.append_log("开始初始化机械臂...")

        try:
            self._services.devices.initialize(ROBOT_SYSTEM)
            self.robot1_connected = True
            self.robot2_connected = True
            self.update_robot_status("robot1", True)
            self.update_robot_status("robot2", True)
            self.refresh_arm_poses()
            self.log_widget.append_log("机械臂初始化完成")
        except Exception as e:
            self.robot1_connected = False
            self.robot2_connected = False
            self.update_robot_status("robot1", False)
            self.update_robot_status("robot2", False)
            self.log_widget.append_log(f"机械臂初始化异常: {str(e)}")

    def initialize_move_controller(self) -> None:
        """初始化底盘移动控制器"""
        try:
            self.log_widget.append_log("初始化底盘移动控制器...")
            self._services.devices.initialize(MOBILE_BASE)
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
            indicator.setStyleSheet("background-color: #22c55e; border-radius: 8px;")
            status_text.setText("已连接")
        else:
            indicator.setStyleSheet("background-color: #ef4444; border-radius: 8px;")
            status_text.setText("未连接")

        self.update_basic_control_buttons()

    def update_hand_status(self, connected: bool):
        """更新末端工具状态指示灯"""
        if connected:
            self.pipette_status_indicator.setStyleSheet("background-color: #22c55e; border-radius: 8px;")
            self.hand_status_text.setText("已连接")
        else:
            self.pipette_status_indicator.setStyleSheet("background-color: #ef4444; border-radius: 8px;")
            self.hand_status_text.setText("未连接")

    def initialize_pipette(self):
        """Initialize the runtime-owned pipette."""
        self.log_widget.append_log("开始初始化移液枪...")
        self.init_pipette_btn.setEnabled(False)
        try:
            success = self._services.manual_control.initialize_pipette()
            self.update_hand_status(bool(success))
            if success:
                self.log_widget.append_log("移液枪初始化成功")
            else:
                self.log_widget.append_log("移液枪初始化失败")
                QMessageBox.warning(self, "警告", "移液枪初始化失败，请检查串口或设备")
        except Exception as e:
            self.update_hand_status(False)
            self.log_widget.append_log(f"移液枪初始化异常: {str(e)}")
            QMessageBox.warning(self, "警告", f"移液枪初始化异常: {e}")
        finally:
            self.init_pipette_btn.setEnabled(True)

    def initialize_pipette_on_startup(self):
        """Initialize pipette automatically when app starts."""
        self.log_widget.append_log("自动初始化移液枪...")
        try:
            success = self._services.manual_control.initialize_pipette()
            self.update_hand_status(bool(success))
            if success:
                self.log_widget.append_log("移液枪初始化成功")
            else:
                self.log_widget.append_log("移液枪初始化失败")
        except Exception as e:
            self.update_hand_status(False)
            self.log_widget.append_log(f"移液枪初始化异常: {str(e)}")

    def eject_pipette_tip(self):
        """Eject pipette tip manually."""
        self.init_pipette_btn.setEnabled(False)
        try:
            self.log_widget.append_log("正在退枪头...")
            success = self._services.manual_control.eject_pipette_tip()
            if success:
                self.log_widget.append_log("枪头已退出")
            else:
                self.log_widget.append_log("退枪头失败")
                QMessageBox.warning(self, "警告", "退枪头失败")
        except Exception as e:
            self.log_widget.append_log(f"退枪头异常: {str(e)}")
            QMessageBox.warning(self, "警告", f"退枪头异常: {e}")
        finally:
            self.init_pipette_btn.setEnabled(True)

    def initialize_body(self):
        """初始化身体（ModbusMotor）"""
        self.log_widget.append_log("开始初始化身体...")

        try:
            self._services.devices.initialize(BODY_AXIS)
            self.body_connected = True
            self.update_body_status(True)
            self.log_widget.append_log("身体初始化成功")
        except Exception as e:
            self.log_widget.append_log(f"身体初始化异常: {str(e)}")
            self.update_body_status(False)

    def update_body_status(self, connected: bool):
        """更新身体状态指示灯"""
        if connected:
            self.body_status_indicator.setStyleSheet("background-color: #22c55e; border-radius: 8px;")
            self.body_status_text.setText("已连接")
        else:
            self.body_status_indicator.setStyleSheet("background-color: #ef4444; border-radius: 8px;")
            self.body_status_text.setText("未连接")

    def _collect_action_names(self) -> set:
        """收集当前所有动作的名称（用于去重校验）"""
        names = set()
        for actions in self.actions.values():
            for a in actions:
                names.add(a.name)
        return names

    def create_action(self):
        current_tab = self.action_tabs.currentIndex()
        resolved = self._resolve_action_type_for_current_tab(current_tab)
        if resolved is None:
            return
        action_type, move_target = resolved if isinstance(resolved, tuple) else (resolved, None)

        if action_type == ActionType.TRAJECTORY:
            self.create_trajectory_action()
            return

        dialog = ActionConfigDialog(
            action_type,
            self.settings.vision,
            localization_reader=self._services.localization.latest,
            existing_names=self._collect_action_names(),
            move_target=move_target,
        )
        if dialog.exec():
            action = dialog.get_action_definition()
            self._services.composition.create_action(
                action,
                origin="gui",
            )

    def create_trajectory_action(self):
        options = ["录制 R1", "录制 R2", "使用已有文件"]
        selected, ok = QInputDialog.getItem(
            self,
            "轨迹动作",
            "创建轨迹动作:",
            options,
            0,
            False
        )
        if not ok:
            return

        if selected == "录制 R1":
            robot_name = "robot1"
            file_path = self.record_trajectory(robot_name)
        elif selected == "录制 R2":
            robot_name = "robot2"
            file_path = self.record_trajectory(robot_name)
        else:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择轨迹文件",
                str(self._trajectory_dir("robot1")),
                "轨迹文件 (*.txt);;所有文件 (*)"
            )
            if not file_path:
                return
            robot_options = ["R1", "R2"]
            robot_selected, robot_ok = QInputDialog.getItem(
                self,
                "轨迹执行机械臂",
                "选择执行轨迹的机械臂:",
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
            "轨迹动作名称",
            "动作名称:",
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
        self._services.composition.create_action(
            action,
            origin="gui",
        )
        self.log_widget.append_log(f"轨迹动作已创建: {name}")

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
                self._services.composition.delete_action(
                    action.id,
                    origin="gui",
                )
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
            self._services.composition.delete_action(
                action.id,
                origin="gui",
            )

    def edit_action(self):
        action_list = self._get_current_action_list_widget()
        if action_list is None:
            return

        current_item = action_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "警告", "请先选择要修改的动作")
            return

        action = current_item.data(Qt.ItemDataRole.UserRole)
        if action is None:
            QMessageBox.warning(self, "警告", "无法读取选中的动作")
            return

        action_data = {
            "id": action.id,
            "name": action.name,
            "parameters": action.parameters
        }
        dialog = ActionConfigDialog(
            action.type,
            self.settings.vision,
            action_data,
            self,
            localization_reader=self._services.localization.latest,
            existing_names=self._collect_action_names(),
        )
        if not dialog.exec():
            return

        updated_action = dialog.get_action_definition()
        try:
            self._services.composition.update_action(
                action.id,
                updated_action,
                origin="gui",
            )
        except KeyError:
            QMessageBox.warning(self, "警告", "未找到目标动作")
            return

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

        if action_type in {ActionType.VISION_CAPTURE, ActionType.VISION_RELOCALIZE}:
            self.vision_capture_list.clear()
            for action in self.actions[ActionType.VISION_CAPTURE]:
                self.vision_capture_list.add_action(action)
            for action in self.actions[ActionType.VISION_RELOCALIZE]:
                self.vision_capture_list.add_action(action)
            return

        list_map = {
            ActionType.INSPECT: self.inspect_list,
            ActionType.CHANGE_GUN: self.change_gun_list,
            ActionType.TRAJECTORY: self.trajectory_list
        }
        action_list = list_map[action_type]
        action_list.clear()

        for action in self.actions[action_type]:
            action_list.add_action(action)

    def load_actions(self):
        all_actions = self._services.composition.list_actions()
        for action_type in self.actions:
            self.actions[action_type].clear()

        for action in all_actions:
            self.actions[action.type].append(action)

        for action_type in self.actions:
            self.refresh_action_list(action_type)

    def _on_composition_changed(
        self,
        event: CompositionEvent,
    ) -> None:
        if event.change_type is CompositionChangeType.ACTIONS:
            self.load_actions()
            return
        if event.change_type is CompositionChangeType.TASKS:
            self.refresh_task_library()
            self._refresh_task_composer_display()
            return
        if event.change_type is CompositionChangeType.SEQUENCE:
            self._sequence_revision = (
                self._services.composition.sequence_revision
            )
            self._render_sequence(
                self._services.composition.sequence_entries()
            )

    def _publish_current_sequence(self) -> None:
        try:
            self._services.composition.replace_sequence(
                self.sequence_list.get_entries(),
                origin="gui",
                expected_revision=self._sequence_revision,
            )
        except CompositionRevisionConflict:
            self._sequence_revision = (
                self._services.composition.sequence_revision
            )
            self._render_sequence(
                self._services.composition.sequence_entries()
            )
            self.log_widget.append_log(
                "序列已被其他入口修改，本次本地编辑未覆盖远程变更"
            )
            return
        self._sequence_revision = (
            self._services.composition.sequence_revision
        )

    def _render_sequence(
        self,
        entries: Sequence[SequenceEntry],
    ) -> None:
        self.sequence_list.blockSignals(True)
        try:
            self.sequence_list.clear_sequence()
            for entry in entries:
                if isinstance(entry, LoopBlock):
                    self.sequence_list.add_loop_block(entry)
                elif isinstance(entry, SequenceItem):
                    self.sequence_list.add_sequence_item(entry)
        finally:
            self.sequence_list.blockSignals(False)

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
            6: ActionType.TRAJECTORY
        }
        if current_tab == 1:
            options = ["执行器动作", "等待"]
            selected, ok = QInputDialog.getItem(
                self,
                "选择动作类型",
                "在执行类下创建:",
                options,
                0,
                False
            )
            if not ok:
                return None
            return ActionType.WAIT if selected == "等待" else ActionType.MANIPULATE
        
        # 移动类 Tab 需要选择具体类型
        if current_tab == 0:
            options = ["机械臂移动", "身体移动", "底盘移动"]
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
            if selected == "底盘移动":
                return ActionType.BASE_MOVE
            else:
                return (ActionType.MOVE, selected)

        if current_tab == 4:
            options = ["视觉抓取", "视觉重定位"]
            selected, ok = QInputDialog.getItem(
                self,
                "选择视觉动作",
                "创建视觉类动作:",
                options,
                0,
                False
            )
            if not ok:
                return None
            return ActionType.VISION_RELOCALIZE if selected == "视觉重定位" else ActionType.VISION_CAPTURE

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
        for summary in self._services.composition.list_tasks():
            task_name = summary.name
            step_count = summary.step_count
            item = QListWidgetItem(f"{task_name} ({step_count} 步)")
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item.setSizeHint(QSize(100, 36))
            item.setIcon(self._create_task_list_icon())
            item.setToolTip(f"{task_name}\n步骤数: {step_count}\n拖到组合计划中")
            item.setData(Qt.ItemDataRole.UserRole, task_name)
            self.task_library_list.addItem(item)

    def _create_task_list_icon(self) -> QIcon:
        from PyQt6.QtGui import QFont, QPainter, QPixmap

        pixmap = QPixmap(28, 28)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(59, 130, 246))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 24, 24, 6, 6)
        # emoji 居中
        font = QFont()
        font.setPointSize(12)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        from PyQt6.QtCore import QRectF
        painter.drawText(QRectF(0, 0, 28, 28), Qt.AlignmentFlag.AlignCenter, "📋")
        painter.end()
        return QIcon(pixmap)

    def add_task_to_composer(self):
        current_item = self.task_library_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "警告", "请先选择一个已保存任务")
            return

        task_name = current_item.data(Qt.ItemDataRole.UserRole)
        self._add_task_name_to_composer(task_name, self.task_composer_list.count())

    def _add_task_name_to_composer(self, task_name: str, insert_row: int | None = None):
        step_count = self._task_step_count(task_name)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {"kind": "task", "task_name": task_name})
        if insert_row is None or insert_row >= self.task_composer_list.count():
            self.task_composer_list.addItem(item)
        else:
            self.task_composer_list.insertItem(max(0, insert_row), item)
        self._refresh_task_composer_display()

        if hasattr(self, "log_widget"):
            self.log_widget.append_log(f"已加入任务组合: {task_name} ({step_count} 步)")

    def _add_action_to_composer(self, action: ActionDefinition, insert_row: int | None = None):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {"kind": "action", "action": action})
        if insert_row is None or insert_row >= self.task_composer_list.count():
            self.task_composer_list.addItem(item)
        else:
            self.task_composer_list.insertItem(max(0, insert_row), item)
        self._refresh_task_composer_display()

        if hasattr(self, "log_widget"):
            self.log_widget.append_log(f"已加入动作组合: {action.name}")

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

    def repeat_composer_selection(self):
        rows = self._selected_contiguous_rows(self.task_composer_list, "请选择要循环的连续任务或动作")
        if rows is None:
            return

        repeat_count, ok = QInputDialog.getInt(
            self,
            "循环执行",
            "循环次数 n:",
            2,
            1,
            999,
            1,
        )
        if not ok or repeat_count <= 1:
            return

        entries = [
            self._clone_composer_entry(self.task_composer_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.task_composer_list.count())
        ]
        start_row = rows[0]
        end_row = rows[-1]
        block = [self._clone_composer_entry(entries[row]) for row in rows]
        insert_at = end_row + 1

        repeated_entries = entries[:insert_at]
        for _ in range(repeat_count - 1):
            repeated_entries.extend(self._clone_composer_entry(entry) for entry in block)
        repeated_entries.extend(entries[insert_at:])

        self.task_composer_list.clear()
        for entry in repeated_entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.task_composer_list.addItem(item)

        self._refresh_task_composer_display()
        for row in range(start_row, start_row + len(block) * repeat_count):
            self.task_composer_list.item(row).setSelected(True)
        self.log_widget.append_log(f"组合块已设置为循环 {repeat_count} 次")

    def clear_task_composer(self):
        self.task_composer_list.clear()

    def expand_composed_tasks(self, replace: bool):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            QMessageBox.warning(self, "警告", "请先向组合计划中添加至少一个任务")
            return

        if replace:
            self._services.composition.replace_sequence(
                sequence,
                origin="gui",
            )
        else:
            self._services.composition.append_sequence(
                sequence,
                origin="gui",
            )

        mode = "替换" if replace else "追加"
        self.log_widget.append_log(f"任务组合已{mode}到序列，共 {len(sequence)} 个动作")

    def save_composed_task(self):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            QMessageBox.warning(self, "警告", "请先向组合计划中添加至少一个任务")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "保存组合任务", "", "任务文件 (*.task)"
        )
        if not filename:
            return

        task_name = Path(filename).name
        stored_name = self._services.composition.save_task(
            task_name,
            sequence,
            origin="gui",
        )
        self.log_widget.append_log(f"组合任务已保存: {stored_name}")

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
            try:
                task_items = (
                    self._services.composition.flattened_task(
                        task_name
                    )
                )
            except FileNotFoundError:
                task_items = ()
            for task_item in task_items:
                cloned_item = SequenceItem(
                    uuid=str(uuid4()),
                    definition=task_item.definition,
                    status=SequenceItemStatus.PENDING,
                )
                sequence.append(cloned_item)
        return sequence

    def _clone_composer_entry(self, entry: dict) -> dict:
        if entry.get("kind") == "action":
            return {"kind": "action", "action": entry["action"]}
        return {"kind": "task", "task_name": entry.get("task_name", "")}

    def _refresh_task_composer_display(self):
        for row in range(self.task_composer_list.count()):
            item = self.task_composer_list.item(row)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry.get("kind") == "action":
                action = entry["action"]
                item.setText(f"{action.name} (动作)")
                item.setIcon(self._create_action_card_icon(action))
                item.setToolTip(f"{action.name}\n类型: {action.type.value}\n拖动可调整顺序")
                continue

            task_name = entry.get("task_name", "")
            step_count = self._task_step_count(task_name)
            item.setText(f"{task_name} ({step_count} 步)")
            item.setIcon(self._create_task_card_icon(task_name, step_count, task_name))
            item.setToolTip(f"{task_name}\n步骤数: {step_count}\n拖动可调整顺序")

    def _task_step_count(self, task_name: str) -> int:
        try:
            return len(
                self._services.composition.flattened_task(task_name)
            )
        except FileNotFoundError:
            return 0

    # ── 动作类型卡片风格（与 widget_components 保持一致）──
    _CARD_STYLE = {
        ActionType.MOVE: ("🦾", QColor(99, 102, 241)),
        ActionType.BASE_MOVE: ("🚗", QColor(239, 68, 68)),
        ActionType.MANIPULATE: ("⚡", QColor(249, 115, 22)),
        ActionType.WAIT: ("⏳", QColor(245, 158, 11)),
        ActionType.INSPECT: ("🔍", QColor(16, 185, 129)),
        ActionType.CHANGE_GUN: ("🔧", QColor(139, 92, 246)),
        ActionType.VISION_CAPTURE: ("👁", QColor(14, 165, 233)),
        ActionType.VISION_RELOCALIZE: ("📍", QColor(6, 182, 212)),
        ActionType.TRAJECTORY: ("📐", QColor(20, 184, 166)),
    }

    _TYPE_LABELS = {
        ActionType.MOVE: "机械臂移动",
        ActionType.BASE_MOVE: "底盘移动",
        ActionType.MANIPULATE: "执行器",
        ActionType.WAIT: "等待",
        ActionType.INSPECT: "检测",
        ActionType.CHANGE_GUN: "换枪",
        ActionType.VISION_CAPTURE: "视觉抓取",
        ActionType.VISION_RELOCALIZE: "视觉重定位",
        ActionType.TRAJECTORY: "轨迹",
    }

    def _create_task_card_icon(self, task_name: str, step_count: int, title: str | None = None):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap

        width, height = 130, 88
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 任务用靛蓝色
        task_color = QColor(59, 130, 246)
        painter.setBrush(task_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(3, 3, width - 6, height - 6, 10, 10)

        # 顶部高光
        highlight = QColor(255, 255, 255, 45)
        painter.setBrush(highlight)
        painter.drawRoundedRect(QRectF(5, 5, width - 10, 16), 6, 6)

        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)

        # 右上角 emoji
        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, width - 8, 30), Qt.AlignmentFlag.AlignRight, "📋")

        # 标题
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        display = task_name[:-5] if task_name.endswith(".task") else task_name
        truncated = display[:14] + "…" if len(display) > 14 else display
        painter.drawText(QRectF(8, 30, width - 16, 24), Qt.AlignmentFlag.AlignLeft, truncated)

        # 类型标签
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(QRectF(8, 50, width - 16, 16), Qt.AlignmentFlag.AlignLeft, "任务组合")

        # 底部状态条
        status_bg = QColor(0, 0, 0, 40)
        painter.setBrush(status_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(5, height - 26, width - 10, 22), 6, 6)

        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRectF(0, height - 26, width, 22),
            Qt.AlignmentFlag.AlignCenter,
            f"{step_count} 步",
        )

        painter.end()
        return QIcon(pixmap)

    def _create_action_card_icon(self, action: ActionDefinition):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap

        width, height = 130, 88
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        emoji, base_color = self._CARD_STYLE.get(
            action.type, ("📋", QColor(148, 163, 184))
        )

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 圆角矩形背景
        painter.setBrush(base_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(3, 3, width - 6, height - 6, 10, 10)

        # 顶部高光条
        highlight = QColor(255, 255, 255, 45)
        painter.setBrush(highlight)
        painter.drawRoundedRect(QRectF(5, 5, width - 10, 16), 6, 6)

        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)

        # 右上角 emoji
        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, width - 8, 30), Qt.AlignmentFlag.AlignRight, emoji)

        # 动作名称
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        truncated = action.name[:14] + "…" if len(action.name) > 14 else action.name
        painter.drawText(
            QRectF(8, 30, width - 16, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            truncated,
        )

        # 类型标签
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 200))
        type_label = self._TYPE_LABELS.get(action.type, action.type.value)
        painter.drawText(QRectF(8, 50, width - 16, 16), Qt.AlignmentFlag.AlignLeft, type_label)

        # 底部状态条
        status_bg = QColor(0, 0, 0, 40)
        painter.setBrush(status_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(5, height - 26, width - 10, 22), 6, 6)

        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRectF(0, height - 26, width, 22),
            Qt.AlignmentFlag.AlignCenter,
            "动作",
        )

        painter.end()
        return QIcon(pixmap)

    def save_task(self):
        entries = self.sequence_list.get_entries()
        if not entries:
            QMessageBox.warning(self, "警告", "序列为空,无需保存")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "保存任务序列", "", "任务文件 (*.task)"
        )
        if filename:
            task_name = Path(filename).name
            self._services.composition.replace_sequence(
                entries,
                origin="gui",
            )
            stored_name = (
                self._services.composition.save_current_task(
                    task_name,
                    origin="gui",
                )
            )
            self.log_widget.append_log(f"任务已保存: {stored_name}")

    def load_task(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "加载任务序列",
            str(self._services.composition.tasks_directory),
            "任务文件 (*.task)",
        )
        if filename:
            task_name = Path(filename).name
            try:
                self._services.composition.load_task_into_sequence(
                    task_name,
                    origin="gui",
                )
            except (FileNotFoundError, ValueError):
                QMessageBox.warning(
                    self,
                    "警告",
                    f"任务不存在: {task_name}",
                )
                return
            self.log_widget.append_log(f"任务已加载: {task_name}")

    def start_execution(self):
        sequence = list(
            self._services.composition.flattened_sequence()
        )
        if not sequence:
            QMessageBox.warning(self, "警告", "请先添加动作到序列中")
            return

        self._start_sequence_execution(sequence, display_list=self.sequence_list, label="动作编排序列")

    def execute_composed_task(self):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            QMessageBox.warning(self, "警告", "请先向组合计划中添加至少一个任务或动作")
            return

        self._start_sequence_execution(sequence, display_list=None, label="任务组合序列")

    def execute_wake_welcome_task(self, task_name: str) -> None:
        """Execute a configured wake lifecycle task without affecting the composer."""
        if self._execution_bridge.is_executing():
            self.log_widget.append_log(f"跳过唤醒欢迎任务，当前已有序列在执行: {task_name}")
            return

        try:
            entries = self._services.composition.load_task(task_name)
        except FileNotFoundError:
            entries = ()
        if not entries:
            self.log_widget.append_log(f"跳过唤醒欢迎任务，任务不存在或为空: {task_name}")
            return

        self._start_sequence_execution(entries, display_list=None, label="唤醒欢迎任务")

    def _start_sequence_execution(self, sequence: list[SequenceItem], display_list=None, label: str = "序列"):
        if self._execution_bridge.is_executing():
            QMessageBox.warning(self, "警告", "当前已有序列正在执行")
            return

        self.log_widget.append_log(f"开始执行{label}...")
        self._execution_display_list = display_list
        self._set_trajectory_buttons_enabled(False)
        self._pause_pose_refresh()

        # 获取执行条目：如果有 display_list（即从序列列表执行），使用树中的 entries
        # 否则（如组合任务执行），使用传入的扁平 sequence
        if display_list is not None:
            entries = list(
                self._services.composition.sequence_entries()
            )
        else:
            entries = list(sequence)  # 扁平列表，无 LoopBlock

        # 重置所有条目的状态
        def _reset_entry(entry):
            if isinstance(entry, LoopBlock):
                entry.current_iteration = 0
                for child in entry.items:
                    child.status = SequenceItemStatus.PENDING
            elif isinstance(entry, SequenceItem):
                entry.status = SequenceItemStatus.PENDING

        for entry in entries:
            _reset_entry(entry)

        # 刷新 UI（仅当有 display_list 时）
        if display_list is not None:
            for i in range(self.sequence_list.topLevelItemCount()):
                tree_item = self.sequence_list.topLevelItem(i)
                entry = tree_item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(entry, LoopBlock):
                    self.sequence_list._update_loop_display(tree_item, entry)
                    for j in range(tree_item.childCount()):
                        child_tree = tree_item.child(j)
                        child_entry = child_tree.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(child_entry, SequenceItem):
                            self.sequence_list._update_item_display(child_tree, child_entry, j)
                elif isinstance(entry, SequenceItem):
                    self.sequence_list._update_item_display(tree_item, entry, i)

        if not self._execution_bridge.execute_sequence_items(
            entries,
            origin="gui",
        ):
            self._set_trajectory_buttons_enabled(True)
            self._resume_pose_refresh()
            QMessageBox.warning(self, "警告", "提交执行失败")

    def toggle_pause(self):
        if self._execution_bridge.is_executing():
            if self.is_paused:
                if not self._execution_bridge.resume_execution():
                    return
                self.control_panel.pause_btn.setText("⏸ 暂停")
                if hasattr(self, 'pause_composed_task_btn'):
                    self.pause_composed_task_btn.setText("⏸ 暂停")
                self.log_widget.append_log("执行继续")
            else:
                if not self._execution_bridge.pause_execution():
                    return
                self.control_panel.pause_btn.setText("▶ 继续")
                if hasattr(self, 'pause_composed_task_btn'):
                    self.pause_composed_task_btn.setText("▶ 继续")
                self.log_widget.append_log("执行暂停")
            self.is_paused = not self.is_paused

    def stop_execution(self):
        if self._execution_bridge.is_executing():
            self._execution_bridge.stop_execution()
            self.log_widget.append_log(
                "已发送任务停止请求（非硬件急停，将在当前动作可中断点停止）"
            )
        else:
            self.log_widget.append_log("当前没有正在执行的任务")

    def request_safety_stop(self, mode: StopMode) -> None:
        if not self._execution_bridge.request_safety_stop(mode):
            self.log_widget.append_log("已有设备停止请求正在处理中")

    def on_execution_completed(self, success: bool):
        self.log_widget.append_log(
            "序列执行成功" if success else "序列执行失败或已停止"
        )
        self.is_paused = False
        self.control_panel.pause_btn.setText("⏸ 暂停")
        if hasattr(self, 'pause_composed_task_btn'):
            self.pause_composed_task_btn.setText("⏸ 暂停")
        self._execution_display_list = self.sequence_list
        self._set_trajectory_buttons_enabled(True)
        self._resume_pose_refresh()
        self.refresh_arm_poses()

    def on_step_started(self, index: int, item: SequenceItem):
        display_list = getattr(self, "_execution_display_list", self.sequence_list)
        if display_list is not None:
            display_list.update_item_status(item)
            tree_item = display_list._find_item_by_entry(item)
            if tree_item is not None:
                display_list.scrollToItem(tree_item)

    def on_step_completed(self, index: int, item: SequenceItem):
        display_list = getattr(self, "_execution_display_list", self.sequence_list)
        if display_list is not None:
            display_list.update_item_status(item)

    def on_step_failed(self, index: int, item: SequenceItem, error_msg: str):
        display_list = getattr(self, "_execution_display_list", self.sequence_list)
        if display_list is not None:
            display_list.update_item_status(item)
        QMessageBox.critical(self, "执行失败", f"步骤 {index + 1} 失败:\n{error_msg}")

    def on_loop_progress(self, loop_uuid: str, current_iteration: int, total_iterations: int):
        """更新循环块执行进度显示"""
        tree_item = self.sequence_list._item_map.get(loop_uuid)
        if tree_item is not None:
            entry = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, LoopBlock):
                entry.current_iteration = current_iteration
                self.sequence_list._update_loop_display(tree_item, entry)

    def move_item_up(self):
        current_row = self.sequence_list.currentRow()
        if current_row > 0:
            item = self.sequence_list.takeItem(current_row)
            self.sequence_list.insertItem(current_row - 1, item)
            self.refresh_sequence_numbers(selected_row=current_row - 1)
            self._publish_current_sequence()

    def move_item_down(self):
        current_row = self.sequence_list.currentRow()
        if current_row < self.sequence_list.count() - 1:
            item = self.sequence_list.takeItem(current_row)
            self.sequence_list.insertItem(current_row + 1, item)
            self.refresh_sequence_numbers(selected_row=current_row + 1)
            self._publish_current_sequence()

    def delete_item(self):
        current_row = self.sequence_list.currentRow()
        if current_row >= 0:
            self.sequence_list.takeItem(current_row)
            next_row = min(current_row, self.sequence_list.count() - 1)
            self.refresh_sequence_numbers(selected_row=next_row)
            self._publish_current_sequence()

    def repeat_sequence_selection(self):
        """将选中的连续动作包裹为 LoopBlock 循环容器"""
        rows = self._selected_contiguous_rows(self.sequence_list, "请选择要循环的连续动作")
        if rows is None:
            return

        repeat_count, ok = QInputDialog.getInt(
            self,
            "循环执行",
            "循环次数 n:",
            2,
            1,
            999,
            1,
        )
        if not ok or repeat_count <= 1:
            return

        # 收集选中的 SequenceItem（从树节点中取出，展开 LoopBlock 的子项）
        selected_items: list[SequenceItem] = []
        for row in rows:
            tree_item = self.sequence_list.topLevelItem(row)
            entry = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, SequenceItem):
                selected_items.append(entry)
            elif isinstance(entry, LoopBlock):
                # 把循环块内的子动作展开收集（保持内容不丢失）
                for child in entry.items:
                    selected_items.append(SequenceItem.from_dict(child.to_dict()))

        if not selected_items:
            QMessageBox.warning(self, "警告", "未找到可循环的动作")
            return

        # 从后往前移除（避免索引偏移）
        for row in reversed(rows):
            self.sequence_list.takeItem(row)

        # 创建 LoopBlock 并插入
        loop = LoopBlock.from_sequence_items(selected_items, repeat_count)
        insert_at = rows[0]
        tree_item = QTreeWidgetItem()
        self.sequence_list._update_loop_display(tree_item, loop)
        tree_item.setData(0, Qt.ItemDataRole.UserRole, loop)
        self.sequence_list._register_item(tree_item, loop)
        for i, child_item in enumerate(loop.items):
            child_tree = QTreeWidgetItem()
            self.sequence_list._update_item_display(child_tree, child_item, i)
            child_tree.setData(0, Qt.ItemDataRole.UserRole, child_item)
            self.sequence_list._register_item(child_tree, child_item)
            tree_item.addChild(child_tree)
        tree_item.setExpanded(True)
        self.sequence_list.insertTopLevelItem(insert_at, tree_item)
        self.sequence_list.setCurrentItem(tree_item)

        total_steps = len(selected_items) * repeat_count
        self.log_widget.append_log(
            f"已创建循环块: {len(selected_items)}个动作 × {repeat_count}次 = {total_steps}步"
        )
        self._publish_current_sequence()

    def _selected_contiguous_rows(self, list_widget: QListWidget, empty_message: str) -> list[int] | None:
        rows = sorted(index.row() for index in list_widget.selectedIndexes())
        if not rows:
            QMessageBox.warning(self, "警告", empty_message)
            return None
        if rows != list(range(rows[0], rows[-1] + 1)):
            QMessageBox.warning(self, "警告", "只能循环连续选中的项目")
            return None
        return rows

    def _clone_sequence_item(self, item: SequenceItem) -> SequenceItem:
        return SequenceItem(
            uuid=str(uuid4()),
            definition=item.definition,
            status=SequenceItemStatus.PENDING,
        )

    def edit_sequence_item(self):
        current_tree_item = self.sequence_list.currentItem()
        if current_tree_item is None:
            QMessageBox.warning(self, "警告", "请先选择要修改的序列项")
            return

        seq_item = current_tree_item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(seq_item, SequenceItem):
            QMessageBox.warning(self, "警告", "请选择一个动作项（不能修改循环块本身）")
            return

        action_def = seq_item.definition
        action_data = {
            "id": action_def.id,
            "name": action_def.name,
            "parameters": action_def.parameters,
        }
        dialog = ActionConfigDialog(
            action_def.type,
            self.settings.vision,
            action_data,
            self,
            localization_reader=self._services.localization.latest,
        )
        if not dialog.exec():
            return

        updated_definition = dialog.get_action_definition()
        seq_item.definition = updated_definition
        self.sequence_list.update_item_status(seq_item)
        self._publish_current_sequence()
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
        if stagger_interval_ms <= 0:
            if replace:
                self._services.composition.replace_sequence(
                    normalized,
                    origin="gui-ai",
                )
            else:
                self._services.composition.append_sequence(
                    normalized,
                    origin="gui-ai",
                )
            self.log_widget.append_log(f"已同步执行序列到右侧，共 {len(normalized)} 个动作")
            return

        from PyQt6.QtCore import QTimer

        if replace:
            self._services.composition.clear_sequence(
                origin="gui-ai",
            )
        self.log_widget.append_log(
            f"正在将 {len(normalized)} 个动作载入右侧序列区（逐项显示）..."
        )

        for i, item in enumerate(normalized):
            item.status = SequenceItemStatus.PENDING

            def make_add(seq_item: SequenceItem):
                def _add():
                    self._services.composition.append_sequence(
                        (seq_item,),
                        origin="gui-ai",
                    )

                return _add

            QTimer.singleShot(stagger_interval_ms * i, make_add(item))

    def clear_sequence(self):
        reply = QMessageBox.question(
            self, "确认", "确定要清空所有序列吗?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._services.composition.clear_sequence(
                origin="gui",
            )
            self.log_widget.append_log("序列已清空")

    def refresh_sequence_numbers(self, selected_row: int | None = None):
        """刷新树中所有顶层项的显示序号"""
        for i in range(self.sequence_list.topLevelItemCount()):
            tree_item = self.sequence_list.topLevelItem(i)
            entry = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, SequenceItem):
                self.sequence_list._update_item_display(tree_item, entry, i)
            elif isinstance(entry, LoopBlock):
                self.sequence_list._update_loop_display(tree_item, entry)
                for j in range(tree_item.childCount()):
                    child_tree = tree_item.child(j)
                    child_entry = child_tree.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(child_entry, SequenceItem):
                        self.sequence_list._update_item_display(child_tree, child_entry, j)
        if selected_row is not None and 0 <= selected_row < self.sequence_list.topLevelItemCount():
            self.sequence_list.setCurrentRow(selected_row)

    def test_camera(self):
        """
        通过 DeviceRuntime 测试相机（与视觉抓取使用同一实例）。
        在独立 QThread 中运行，避免阻塞 UI。
        """
        self.test_camera_btn.setEnabled(False)
        self.test_camera_btn.setText("测试中...")

        class _TestWorker(QThread):
            result = pyqtSignal(bool, str)

            def __init__(self, services: ApplicationServices):
                super().__init__()
                self._services = services

            def run(self):
                import time

                camera_name = (
                    self.settings.vision.vision_camera_name
                    or None
                )
                session = None

                try:
                    session = self._services.camera_access.open(
                        "gui-test"
                    )
                    mgr = session.camera

                    # 等待至少一路相机上线
                    deadline = time.time() + 10
                    online = []
                    while time.time() < deadline:
                        info = mgr.get_cameras_info()
                        online = [c for c in info if c.get("online")]
                        if online:
                            break
                        time.sleep(0.3)
                    else:
                        all_info = mgr.get_cameras_info()
                        errors = []
                        for c in all_info:
                            if not c.get("online"):
                                errors.append(f"{c.get('name', '?')}: {c.get('error', '未知')}")
                        if errors:
                            self.result.emit(False, f"相机启动失败: {'; '.join(errors)}")
                        else:
                            self.result.emit(False, "未检测到在线相机")
                        return

                    # 尝试取帧
                    deadline = time.time() + 10
                    while time.time() < deadline:
                        if hasattr(mgr, "get_latest_raw_frames"):
                            # RealSense：取原始帧（与视觉抓取 executor.py 一致）
                            raw = mgr.get_latest_raw_frames(camera_name)
                            if raw is not None:
                                color, depth, intr = raw
                                if color is not None and depth is not None:
                                    h, w = color.shape[:2]
                                    center_dist = float(depth[h // 2, w // 2])
                                    actual_name = camera_name or online[0]["name"]
                                    sn = ""
                                    for c in online:
                                        if c["name"] == actual_name:
                                            sn = f" SN={c['serial']}"
                                            break
                                    msg = (f"成功: 彩色={w}x{h}  "
                                           f"深度(中心)={center_dist / 1000:.3f}m  "
                                           f"(相机={actual_name}{sn})")
                                    self.result.emit(True, msg)
                                    return
                        else:
                            # OpenCV / Webcam：取 JPEG 帧
                            jpegs = mgr.get_latest_jpegs()
                            if jpegs:
                                if camera_name:
                                    matched = [(n, len(b)) for s, n, b in jpegs if n == camera_name]
                                    if matched:
                                        self.result.emit(True, f"成功: webcam 已取到帧 (相机={matched[0][0]}, {matched[0][1]} bytes)")
                                        return
                                else:
                                    name = jpegs[0][1]
                                    self.result.emit(True, f"成功: webcam 已取到帧 (相机={name})")
                                    return
                        time.sleep(0.2)

                    self.result.emit(False, "取帧超时（10 秒内未获得有效帧）")

                except Exception as e:
                    self.result.emit(False, f"测试异常: {str(e)}")
                finally:
                    if session is not None:
                        session.close()

        def on_result(success, msg):
            self.log_widget.append_log(f"[相机测试] {msg}")
            self.test_camera_btn.setEnabled(True)
            self.test_camera_btn.setText("测试相机")

        self._camera_test_thread = _TestWorker(self._services)
        self._camera_test_thread.result.connect(on_result)
        self._camera_test_thread.start()

    def closeEvent(self, event):
        if self.pose_timer is not None:
            self.pose_timer.stop()
        if hasattr(self, "ai_assistant_widget"):
            self.ai_assistant_widget.shutdown()
        self._composition_bridge.close()
        self._startup_lifecycle.close()
        event.accept()
