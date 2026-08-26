from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
)

from ..bridges.execution import ExecutionBridge
from ..app_dialogs import ask_integer, ask_text, choose_item
from ..application_lifecycle import (
    begin_gui_shutdown,
    install_gui_application_lifecycle,
)
from ..about import show_about_dialog
from ..branding import APPLICATION_NAME
from ...application import (
    ApplicationServices,
    CompositionChangeType,
    CompositionEvent,
    CompositionRevisionConflict,
    WorkflowCompilationError,
)
from ...application.camera_access import CameraSession
from ...configuration.settings import CameraRole
from ...domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    ParallelBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
    SubworkflowBlock,
)
from ...domain.workflow import WorkflowDocument
from ...devices import CameraSource, DepthCameraSource, StopMode
from ...devices.runtime.ids import (
    BODY_AXIS,
    MOBILE_BASE,
    PIPETTE,
    ROBOT_SYSTEM,
)
from ..views.log_widget import LogFilter, LogWidget
from ..views.ai_assistant import AIAssistantWidget
from ..views.action_list import ActionListWidget
from ..bridges.composition import CompositionBridge
from ..views.device import DeviceControlView, DeviceHealthView, DevicePoseView
from ..views.dialogs import ActionConfigDialog
from ..views.action_picker import ActionPickerDialog
from ..bridges.notifications import GuiNotificationCenter
from .startup import (
    GuiHardwareStartupWorker,
    GuiStartupLifecycle,
    GuiStartupState,
    HardwareStartupStepResult,
)
from ..view_models.models import DeviceViewModel, ExecutionViewModel
from ..views.workflow import (
    ActionLibraryView,
    TaskLibraryView,
    WorkflowEditorView,
)
from ..views.workflow_canvas import WorkflowCanvasWidget
from ..views.workbench import WorkbenchPage, WorkbenchView
from ..theme import ThemeController, ThemeMode, ThemeTransitionOverlay
from ..icons import IconName, themed_icon
from ..shortcuts import ShortcutRegistry
from ..workbench_layout import WorkbenchLayoutStore
from ..window_chrome import ApplicationTitleBar, RoundedMainWindow


_WORKFLOW_FILE_SUFFIX = ".workflow.json"

logger = logging.getLogger(__name__)


def _display_task_name(task_name: str) -> str:
    """Hide the persistence suffix without changing the stored task identity."""
    return task_name.removesuffix(_WORKFLOW_FILE_SUFFIX)


class MainWindow(RoundedMainWindow):
    startup_progress_changed = Signal(int, str, str)
    startup_finished = Signal(bool, str)

    def __init__(
        self,
        services: ApplicationServices,
        theme_controller: ThemeController,
        layout_store: WorkbenchLayoutStore | None = None,
    ) -> None:
        application = QApplication.instance()
        if application is not None:
            install_gui_application_lifecycle(application)
        super().__init__()
        self._services = services
        self._theme_controller = theme_controller
        self._layout_store = layout_store
        self._shortcut_registry = ShortcutRegistry(parent=self)
        self._menu_actions: dict[str, QAction] = {}
        self._theme_controller.setParent(self)
        self._execution_bridge = ExecutionBridge(services)
        self._device_view_model = DeviceViewModel(services.devices)
        self._execution_view_model = ExecutionViewModel(services.execution)
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
        self.settings = services.settings
        self.robot_pose_cache: dict[str, list[float] | None] = {
            "robot1": None,
            "robot2": None,
        }
        self.pose_timer: QTimer | None = None
        self._startup_lifecycle = GuiStartupLifecycle()
        self._speech_startup_wait_timer: QTimer | None = None
        self._hardware_startup_thread: QThread | None = None
        self._hardware_startup_worker: GuiHardwareStartupWorker | None = None
        self._camera_test_thread: QThread | None = None
        self._startup_begin_timer = QTimer(self)
        self._startup_begin_timer.setSingleShot(True)
        self._startup_begin_timer.timeout.connect(self.start_startup_initialization)

        self.init_ui()
        self._execution_display_list: WorkflowCanvasWidget | None = (
            self.workflow_view.sequence_list
        )
        self._notifications = GuiNotificationCenter(
            self,
            log_sink=self.log_widget.append_notification,
            toast_sink=self.workbench_view.show_notification,
        )
        if self.workbench_view.layout_recovery_reason is not None:
            self._notifications.warning(
                "工作台布局偏好已损坏，已恢复默认布局",
                modal=False,
            )
        self._composition_bridge = CompositionBridge(
            services.composition,
            self,
        )
        self._composition_bridge.changed.connect(
            self._on_composition_changed,
            Qt.ConnectionType.AutoConnection,
        )
        self.workflow_view.sequence_list.sequence_changed.connect(
            self._publish_current_sequence
        )
        self.load_actions()
        self.refresh_task_library()
        initial_entries = self._services.composition.sequence_entries()
        if initial_entries:
            state = self._services.workflow_editing.snapshot()
            document = WorkflowDocument.from_entries(
                workflow_id=state.document.workflow_id,
                name=state.document.name,
                revision=state.document.revision,
                entries=initial_entries,
                robot_profile_id=state.document.robot_profile_id,
            )
            self._services.workflow_editing.replace_document(document)
        self._render_sequence(
            self._services.workflow_editing.snapshot().document.to_entries()
        )
        self._execution_bridge.step_started.connect(self.on_step_started)
        self._execution_bridge.step_completed.connect(self.on_step_completed)
        self._execution_bridge.step_failed.connect(self.on_step_failed)
        self._execution_bridge.log_message.connect(self._notifications.info)
        self._execution_bridge.loop_progress.connect(self.on_loop_progress)
        self._execution_bridge.parallel_branch_state.connect(
            self.on_parallel_branch_state
        )
        self._execution_bridge.execution_completed.connect(
            self.on_execution_completed
        )
        self._execution_bridge.execution_status_changed.connect(
            lambda _status: self._render_execution_state()
        )
        self._render_execution_state()

        ai_assistant = self.ai_assistant_view
        if ai_assistant is not None:
            ai_assistant.speech_runtime_startup_finished.connect(
                self._on_speech_runtime_startup_finished
            )
            ai_assistant.welcome_workflow_execution_requested.connect(
                self.execute_wake_welcome_workflow
            )
            ai_assistant.sequence_visualization_requested.connect(
                self.add_ai_sequence
            )
            ai_assistant.step_started.connect(self.on_step_started)
            ai_assistant.step_completed.connect(self.on_step_completed)
            ai_assistant.step_failed.connect(self.on_step_failed)
            ai_assistant.loop_progress.connect(self.on_loop_progress)
            ai_assistant.execution_completed.connect(
                self.on_execution_completed
            )
        self._startup_begin_timer.start(0)

    @property
    def startup_state(self) -> GuiStartupState:
        return self._startup_lifecycle.state

    def start_startup_initialization(self) -> None:
        """启动 GUI 显示前的必要初始化流程。"""
        if not self._startup_lifecycle.begin():
            return

        self.startup_progress_changed.emit(
            24,
            "正在准备语音与设备运行时...",
            "",
        )

        try:
            speech_start_requested = False
            ai_assistant = self.ai_assistant_view
            if ai_assistant is not None:
                speech_start_requested = (
                    ai_assistant.start_voice_speech_runtime_if_configured()
                )
        except Exception:
            self._startup_lifecycle.mark_failed()
            raise

        if not speech_start_requested:
            self.startup_progress_changed.emit(
                36,
                "语音输入未启用，开始初始化设备...",
                "",
            )
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
        self.startup_progress_changed.emit(
            32,
            "正在加载 ASR / KWS 模型...",
            "",
        )

    def _on_speech_runtime_startup_finished(self, speech_ready: bool) -> None:
        if self.startup_state is not GuiStartupState.WAITING_FOR_SPEECH:
            return
        self.startup_progress_changed.emit(
            48,
            "语音监听已就绪" if speech_ready else "语音初始化不可用",
            "" if speech_ready else "语音失败不阻止设备控制界面启动",
        )
        self.initialize_startup_hardware(speech_ready)

    def initialize_startup_hardware(
        self,
        _speech_ready: bool = False,
    ) -> None:
        """Start hardware initialization without blocking the GUI thread."""
        if not self._startup_lifecycle.begin_hardware_initialization():
            return
        if self._speech_startup_wait_timer is not None:
            self._speech_startup_wait_timer.stop()
            self._speech_startup_wait_timer = None

        thread = QThread(self)
        thread.setObjectName("GuiHardwareStartupThread")
        worker = GuiHardwareStartupWorker(
            self._services,
            initialize_mobile_base=self.settings.devices.body_di_pan,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.step_started.connect(self._on_hardware_startup_step_started)
        worker.step_completed.connect(self._on_hardware_startup_step_completed)
        worker.completed.connect(self._on_hardware_startup_completed)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(
            thread.quit,
            Qt.ConnectionType.DirectConnection,
        )
        thread.finished.connect(self._on_hardware_startup_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._hardware_startup_thread = thread
        self._hardware_startup_worker = worker
        thread.start()

    def _on_speech_startup_wait_timeout(self) -> None:
        """Continue hardware startup if ASR/KWS first-load is still downloading."""
        if self.startup_state is not GuiStartupState.WAITING_FOR_SPEECH:
            return
        self.ai_assistant_view.notify_speech_startup_wait_timeout()
        self.startup_progress_changed.emit(
            44,
            "语音模型继续后台加载，开始初始化设备...",
            "语音就绪后会自动开始监听，不阻塞设备初始化",
        )
        self.initialize_startup_hardware(False)

    def _on_hardware_startup_step_started(self, device_id: str) -> None:
        if self.startup_state is GuiStartupState.CLOSED:
            return
        progress = {
            ROBOT_SYSTEM: (54, "正在连接机械臂..."),
            MOBILE_BASE: (66, "正在连接移动底盘..."),
            BODY_AXIS: (76, "正在初始化身体控制器..."),
            PIPETTE: (88, "正在初始化移液枪..."),
        }
        percent, message = progress.get(device_id, (60, "正在初始化设备..."))
        self.startup_progress_changed.emit(percent, message, "")

    def _on_hardware_startup_step_completed(
        self,
        result: HardwareStartupStepResult,
    ) -> None:
        if self.startup_state is GuiStartupState.CLOSED:
            return
        progress = {
            ROBOT_SYSTEM: 64,
            MOBILE_BASE: 74,
            BODY_AXIS: 86,
            PIPETTE: 94,
        }
        state = "完成" if result.succeeded else "不可用，稍后可在主界面重试"
        self.startup_progress_changed.emit(
            progress.get(result.device_id, 90),
            f"{self._startup_device_name(result.device_id)}{state}",
            result.error or "",
        )

    def _on_hardware_startup_completed(
        self,
        results: tuple[HardwareStartupStepResult, ...],
    ) -> None:
        if self.startup_state is GuiStartupState.CLOSED:
            return
        self._render_device_state()
        failures = tuple(result for result in results if not result.succeeded)
        for result in failures:
            self._notifications.warning(
                f"{self._startup_device_name(result.device_id)}初始化失败：{result.error}",
                modal=False,
            )
        self._startup_lifecycle.mark_ready()
        if self.pose_timer is not None and not self.pose_timer.isActive():
            self.pose_timer.start()
        if failures:
            message = f"初始化完成，{len(failures)} 个设备暂不可用"
        else:
            message = "所有必要组件初始化完成"
        self.startup_progress_changed.emit(96, message, "")
        self.startup_finished.emit(True, message)

    def _on_hardware_startup_thread_finished(self) -> None:
        self._hardware_startup_thread = None
        self._hardware_startup_worker = None

    @staticmethod
    def _startup_device_name(device_id: str) -> str:
        return {
            ROBOT_SYSTEM: "机械臂",
            MOBILE_BASE: "移动底盘",
            BODY_AXIS: "身体控制器",
            PIPETTE: "移液枪",
        }.get(device_id, device_id)

    def init_ui(self) -> None:
        self.setWindowTitle(APPLICATION_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(540, 800)
        self.resize(900, 960)

        self.task_library_view = TaskLibraryView()
        self.action_library_view = ActionLibraryView()
        self.ai_assistant_view = AIAssistantWidget(self._services)
        self.workflow_view = WorkflowEditorView()
        canvas_scale_provider = self.workflow_view.sequence_list.display_scale
        self.task_library_view.set_canvas_scale_provider(canvas_scale_provider)
        self.action_library_view.set_canvas_scale_provider(canvas_scale_provider)
        self.device_status_view = DeviceHealthView()
        self.device_pose_view = DevicePoseView()
        self.device_control_view = DeviceControlView()
        self.log_widget = LogWidget()
        self.workbench_view = WorkbenchView(
            side_pages=(
                WorkbenchPage(
                    key="tasks",
                    title="已保存任务",
                    icon=IconName.TASKS,
                    widget=self.task_library_view,
                ),
                WorkbenchPage(
                    key="actions",
                    title="基础动作",
                    icon=IconName.ACTIONS,
                    widget=self.action_library_view,
                ),
                WorkbenchPage(
                    key="assistant",
                    title="AI 助手",
                    icon=IconName.ASSISTANT,
                    widget=self.ai_assistant_view,
                ),
            ),
            editor=self.workflow_view,
            bottom_pages=(
                WorkbenchPage("devices", "设备", IconName.DEVICES, self.device_status_view),
                WorkbenchPage("poses", "位姿", IconName.POSES, self.device_pose_view),
                WorkbenchPage("controls", "控制", IconName.CONTROLS, self.device_control_view),
                WorkbenchPage("logs", "日志", IconName.LOGS, self.log_widget),
            ),
            layout_store=self._layout_store,
        )
        self.setCentralWidget(self.workbench_view)
        self.create_menu()
        self._theme_transition = ThemeTransitionOverlay(self)
        self._connect_view_signals()

        self.pose_timer = QTimer(self)
        self.pose_timer.setInterval(1000)
        self.pose_timer.timeout.connect(self.refresh_arm_poses)

    def _connect_view_signals(self) -> None:
        self.log_widget.counts_changed.connect(
            self.workbench_view.status_bar.render_log_counts
        )
        self.workbench_view.status_bar.log_filter_requested.connect(
            self.log_widget.set_filter
        )
        library = self.action_library_view
        library.create_requested.connect(self.create_action)
        library.edit_requested.connect(self.edit_action)
        library.delete_requested.connect(self.delete_action)
        library.camera_test_requested.connect(self.test_camera)
        library.action_insert_requested.connect(
            self._insert_action_from_library
        )
        self.task_library_view.task_open_requested.connect(
            self.open_selected_task
        )
        self.task_library_view.task_insert_requested.connect(
            self.insert_selected_task
        )

        workflow = self.workflow_view
        workflow.save_requested.connect(self.save_task)
        workflow.clear_requested.connect(self.clear_sequence)
        workflow.start_requested.connect(self.start_execution)
        workflow.pause_requested.connect(self.toggle_pause)
        workflow.stop_requested.connect(self.stop_execution)
        workflow.safety_stop_requested.connect(self.request_safety_stop)
        workflow.move_up_requested.connect(self.move_item_up)
        workflow.move_down_requested.connect(self.move_item_down)
        workflow.edit_requested.connect(self.edit_sequence_item)
        workflow.repeat_requested.connect(self.repeat_sequence_selection)
        workflow.delete_requested.connect(self.delete_item)
        workflow.insert_action_at_requested.connect(
            self._choose_action_for_insertion
        )
        workflow.insert_action_in_loop_requested.connect(
            self._choose_action_for_loop_insertion
        )
        workflow.insert_action_in_parallel_requested.connect(
            self._choose_action_for_parallel_insertion
        )
        workflow.add_parallel_branch_requested.connect(
            self._choose_action_for_new_parallel_branch
        )
        workflow.insert_subworkflow_requested.connect(
            self._insert_subworkflow_by_name
        )
        workflow.insert_subworkflow_in_loop_requested.connect(
            self._insert_subworkflow_into_loop_by_name
        )
        self.device_pose_view.refresh_requested.connect(self.refresh_arm_poses)
        self.device_pose_view.copy_pose_requested.connect(self.copy_robot_pose)
        controls = self.device_control_view
        controls.gripper_requested.connect(self._set_gripper_state)
        controls.relay_requested.connect(self._set_relay_state)
        controls.pipette_eject_requested.connect(self.eject_pipette_tip)

    def create_menu(self) -> None:
        title_bar = ApplicationTitleBar(self)
        menubar = title_bar.menu_bar
        self.setMenuWidget(title_bar)
        self.application_title_bar = title_bar
        self.application_menu_bar = menubar

        file_menu = menubar.addMenu("文件")
        self._add_menu_action(file_menu, "file.save", "保存当前任务", self.save_task)
        self._add_menu_action(file_menu, "file.save_as", "另存为任务", self.save_task_as)
        self._add_menu_action(file_menu, "file.open", "加载任务", self.load_task)

        file_menu.addSeparator()
        self._add_menu_action(file_menu, "file.exit", "退出", self.close)

        edit_menu = menubar.addMenu("编辑")
        for command_id, label, callback in (
            ("edit.undo", "撤销", self.workflow_view.sequence_list.undo),
            ("edit.redo", "重做", self.workflow_view.sequence_list.redo),
            ("edit.modify", "修改节点", self.edit_sequence_item),
            ("edit.delete", "删除节点", self.delete_item),
            ("edit.clear", "清空工作流", self.clear_sequence),
        ):
            self._add_menu_action(edit_menu, command_id, label, callback)

        view_menu = menubar.addMenu("视图")
        self._add_menu_action(
            view_menu,
            "view.sidebar",
            "资源侧栏",
            self.workbench_view.toggle_last_side_page
        )
        panel_menu = QMenu("底部面板", view_menu)
        view_menu.addMenu(panel_menu)
        panel_actions: dict[str, QAction] = {}
        for label, key in (
            ("设备", "devices"),
            ("机械臂位姿", "poses"),
            ("基础控制", "controls"),
            ("运行日志", "logs"),
        ):
            def toggle_panel(
                _checked: bool = False,
                *,
                page_key: str = key,
            ) -> None:
                if page_key == "logs":
                    self.log_widget.set_filter(LogFilter.ALL)
                    self.workbench_view.toggle_log_filter(LogFilter.ALL)
                    return
                self.workbench_view.toggle_bottom_page(page_key)

            command_id = f"view.{key}"
            panel_actions[key] = self._add_menu_action(
                panel_menu,
                command_id,
                label,
                toggle_panel,
            )
        view_menu.addSeparator()
        theme_menu = QMenu("主题", view_menu)
        view_menu.addMenu(theme_menu)
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for command_id, label, mode in (
            ("view.theme_system", "跟随系统", ThemeMode.SYSTEM),
            ("view.theme_light", "浅色", ThemeMode.LIGHT),
            ("view.theme_dark", "深色", ThemeMode.DARK),
        ):
            def apply_theme(
                _checked: bool = False,
                *,
                selected: ThemeMode = mode,
            ) -> None:
                origin = self.mapFromGlobal(QCursor.pos())
                self._theme_transition.request_mode(
                    self._theme_controller,
                    selected,
                    origin,
                )

            action = self._add_menu_action(
                theme_menu,
                command_id,
                label,
                apply_theme,
                checkable=True,
            )
            action.setData(mode.value)
            action.setChecked(mode is self._theme_controller.mode)
            theme_group.addAction(action)
        self._theme_actions = {
            ThemeMode(action.data()): action
            for action in theme_group.actions()
        }
        self._theme_controller.mode_changed.connect(
            self._sync_theme_menu,
            Qt.ConnectionType.QueuedConnection,
        )
        view_menu.addSeparator()
        self._add_menu_action(
            view_menu,
            "view.reset_layout",
            "恢复默认布局",
            self.workbench_view.reset_layout,
        )
        execution_menu = menubar.addMenu("执行")
        for command_id, label, callback in (
            ("execution.start", "开始执行", self.start_execution),
            ("execution.pause", "暂停/恢复", self.toggle_pause),
            ("execution.stop", "停止任务", self.stop_execution),
            ("execution.quick_stop", "快速停止", lambda: self.request_safety_stop(StopMode.QUICK)),
            ("execution.emergency", "设备急停", lambda: self.request_safety_stop(StopMode.EMERGENCY)),
        ):
            self._add_menu_action(execution_menu, command_id, label, callback)

        device_menu = menubar.addMenu("设备")
        self._add_menu_action(
            device_menu,
            "device.refresh_pose",
            "刷新机械臂位姿",
            self.refresh_arm_poses,
        )
        device_menu.addAction(panel_actions["controls"])

        help_menu = menubar.addMenu("帮助")
        self._add_menu_action(
            help_menu,
            "view.shortcuts",
            "快捷键设置…",
            lambda: self._shortcut_registry.open_editor(self),
        )
        help_menu.addSeparator()
        self._add_menu_action(
            help_menu,
            "help.about",
            f"关于 {APPLICATION_NAME}",
            lambda: show_about_dialog(self),
        )

    def _add_menu_action(
        self,
        menu: QMenu,
        command_id: str,
        label: str,
        callback: Callable[[], object],
        *,
        checkable: bool = False,
    ) -> QAction:
        """Create one menu action bound through the application shortcut registry."""
        action = QAction(label, self)
        action.setCheckable(checkable)
        action.triggered.connect(callback)
        self._shortcut_registry.register(command_id, action)
        # The logical QMenu is not a visible native menu. Associate every
        # command with the window explicitly so its centralized shortcut stays
        # active while the in-window overlay is closed.
        self.addAction(action)
        menu.addAction(action)
        self._menu_actions[command_id] = action
        return action

    def _sync_theme_menu(self, mode: object) -> None:
        if isinstance(mode, ThemeMode):
            self._theme_actions[mode].setChecked(True)

    def _set_relay_state(self, channel: int, turn_on: bool) -> None:
        action_text = "打开" if turn_on else "关闭"
        try:
            self._services.manual_control.set_relay(channel, turn_on)
            self._notifications.info(f"继电器 Y{channel} 已{action_text}")
        except Exception as e:
            self._notifications.warning(
                f"继电器 Y{channel} {action_text}失败:\n{e}"
            )

    def _set_gripper_state(self, opened: bool) -> None:
        if not self._device_view_model.snapshot().robot_ready:
            self._notifications.warning("Robot1 未连接")
            return

        action = "打开" if opened else "关闭"
        try:
            success = self._services.manual_control.set_gripper(
                "left",
                opened=opened,
            )
            if success:
                self._notifications.info(f"Robot1 夹爪已{action}")
            else:
                self._notifications.warning(f"Robot1 夹爪{action}失败")
        except Exception as e:
            self._notifications.warning(f"Robot1 夹爪{action}异常: {e}")

    def record_trajectory(self, robot_name: str) -> str | None:
        if not self._device_view_model.snapshot().robot_ready:
            self._notifications.warning(f"{robot_name.upper()} 未连接")
            return None

        teaching_started = False
        try:
            self._notifications.info(f"{robot_name.upper()} 开始拖动示教")
            self._services.trajectory_teaching.start(robot_name)
            teaching_started = True

            self._notifications.info(
                f"{robot_name.upper()} 正在录制。请手动拖动机械臂，完成后点击确定停止并保存。",
                title="轨迹录制",
                modal=True,
            )

            save_result = self._services.trajectory_teaching.stop_and_save()
            teaching_started = False
            self._notifications.info(
                f"{robot_name.upper()} 轨迹已保存: "
                f"{save_result.path}, 点数: {save_result.point_count}"
            )
            self._notifications.info(
                f"保存到:\n{save_result.path}",
                title="轨迹已保存",
                modal=True,
            )
            return str(save_result.path)
        except Exception as e:
            if teaching_started:
                try:
                    self._services.trajectory_teaching.cancel()
                except Exception as stop_error:
                    self._notifications.warning(
                        f"{robot_name.upper()} 停止拖动示教失败: "
                        f"{stop_error}",
                        modal=False,
                    )
            self._notifications.warning(f"轨迹录制异常: {e}")
        return None

    def run_trajectory(self, robot_name: str) -> None:
        if not self._device_view_model.snapshot().robot_ready:
            self._notifications.warning(f"{robot_name.upper()} 未连接")
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

        try:
            trajectory_path = self._services.trajectory_teaching.import_trajectory(
                robot_name,
                filename,
            )
        except (OSError, ValueError) as exc:
            self._notifications.warning(f"轨迹文件导入失败: {exc}")
            return

        action = ActionDefinition(
            id=str(uuid4()),
            name=f"{robot_name.upper()} {trajectory_path.stem}",
            type=ActionType.TRAJECTORY,
            parameters={
                "robot": robot_name,
                "file_path": str(trajectory_path),
            },
        )
        self._start_sequence_execution([SequenceItem.from_definition(action)], display_list=None, label="轨迹")

    def on_trajectory_succeeded(self, message: str) -> None:
        self._notifications.info(message, title="轨迹", modal=True)

    def on_trajectory_failed(self, message: str) -> None:
        self._notifications.warning(message, title="轨迹")

    def _trajectory_dir(self, robot_name: str) -> Path:
        return self._services.trajectory_teaching.trajectory_directory(robot_name)

    def _set_trajectory_buttons_enabled(self, enabled: bool) -> None:
        self._render_device_state()
        if not enabled:
            for attr in (
                "record_robot1_path_btn",
                "run_robot1_path_btn",
                "record_robot2_path_btn",
                "run_robot2_path_btn",
            ):
                if hasattr(self, attr):
                    getattr(self, attr).setEnabled(False)

    def _pause_pose_refresh(self) -> None:
        if self.pose_timer is not None and self.pose_timer.isActive():
            self.pose_timer.stop()

    def _resume_pose_refresh(self) -> None:
        if self.pose_timer is not None and not self.pose_timer.isActive():
            self.pose_timer.start()

    def refresh_arm_poses(self) -> None:
        self._refresh_single_robot_pose("robot1")
        self._refresh_single_robot_pose("robot2")
        self.refresh_external_localization()

    def refresh_external_localization(self) -> None:
        try:
            receiver = self._services.external_localization
            position = receiver.latest(
                max_age=10.0,
                valid_only=False,
                wait_timeout=0.0,
            )
            if position is None:
                error = receiver.last_error
                text = f"UDP -- ({error})" if error else "UDP --"
                self.device_pose_view.render_localization(text)
                return
        except Exception as exc:
            self.device_pose_view.render_localization(f"UDP error: {exc}")
            return

        self.device_pose_view.render_localization(
            self.format_external_localization_text(position)
        )

    def _refresh_single_robot_pose(self, robot_name: str) -> None:
        pose = self._get_current_pose(robot_name)
        if pose is None:
            self.robot_pose_cache[robot_name] = None
            self.device_pose_view.render_pose(robot_name, "--")
            return

        self.robot_pose_cache[robot_name] = pose
        self.device_pose_view.render_pose(robot_name, self.format_pose_text(pose))

    def _get_current_pose(self, robot_name: str) -> list[float] | None:
        try:
            state = self._services.robot_query.try_read_state(robot_name)
            if state is None:
                return self.robot_pose_cache.get(robot_name)
            return state.pose.to_list()
        except Exception:
            return None

    def _read_current_arm_pose_for_form(self, arm: str) -> list[float]:
        state = self._services.robot_query.try_read_state(arm)
        if state is None:
            raise RuntimeError(
                f"{arm}臂当前位姿不可用，请确认设备已连接并完成初始化"
            )
        return state.pose.to_list()

    def format_pose_text(self, pose: Sequence[float]) -> str:
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

    def format_external_localization_text(
        self,
        position: Mapping[str, int | float],
    ) -> str:
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

    def copy_robot_pose(self, robot_name: str) -> None:
        pose = self.robot_pose_cache.get(robot_name)
        if pose is None:
            self._refresh_single_robot_pose(robot_name)
            pose = self.robot_pose_cache.get(robot_name)

        if pose is None:
            self._notifications.warning(f"{robot_name.upper()} 位姿不可用")
            return

        pose_text = f"[{', '.join([f'{v:.6f}' for v in pose])}]"
        QApplication.clipboard().setText(pose_text)
        self._notifications.info(f"已复制 {robot_name.upper()} 位姿: {pose_text}")

    def initialize_robots(self) -> None:
        """初始化机械臂"""
        self._notifications.info("开始初始化机械臂...")

        try:
            self._services.devices.initialize(ROBOT_SYSTEM)
            self._render_device_state()
            self.refresh_arm_poses()
            self._notifications.info("机械臂初始化完成")
        except Exception as e:
            self._render_device_state()
            self._notifications.warning(
                f"机械臂初始化异常: {e}",
                modal=False,
            )

    def initialize_move_controller(self) -> None:
        """初始化底盘移动控制器"""
        try:
            self._notifications.info("初始化底盘移动控制器...")
            self._services.devices.initialize(MOBILE_BASE)
            self._notifications.info("底盘移动控制器初始化成功")
        except Exception as e:
            self._notifications.warning(
                f"底盘移动控制器初始化失败：{e}",
                modal=False,
            )

    def _render_device_state(self) -> None:
        state = self._device_view_model.snapshot()
        self.device_status_view.render_state(state)
        self.workbench_view.status_bar.render_device_state(state)
        self.device_control_view.render_state(state)

    def initialize_pipette(self) -> None:
        """Initialize the runtime-owned pipette."""
        self._notifications.info("开始初始化移液枪...")
        self.device_control_view.set_pipette_action_enabled(False)
        try:
            success = self._services.manual_control.initialize_pipette()
            self._render_device_state()
            if success:
                self._notifications.info("移液枪初始化成功")
            else:
                self._notifications.warning(
                    "移液枪初始化失败，请检查串口或设备"
                )
        except Exception as e:
            self._render_device_state()
            self._notifications.warning(f"移液枪初始化异常: {e}")
        finally:
            self.device_control_view.set_pipette_action_enabled(True)

    def initialize_pipette_on_startup(self) -> None:
        """Initialize pipette automatically when app starts."""
        self._notifications.info("自动初始化移液枪...")
        try:
            success = self._services.manual_control.initialize_pipette()
            self._render_device_state()
            if success:
                self._notifications.info("移液枪初始化成功")
            else:
                self._notifications.warning("移液枪初始化失败", modal=False)
        except Exception as e:
            self._render_device_state()
            self._notifications.warning(
                f"移液枪初始化异常: {e}",
                modal=False,
            )

    def eject_pipette_tip(self) -> None:
        """Eject pipette tip manually."""
        self.device_control_view.set_pipette_action_enabled(False)
        try:
            self._notifications.info("正在退枪头...")
            success = self._services.manual_control.eject_pipette_tip()
            if success:
                self._notifications.info("枪头已退出")
            else:
                self._notifications.warning("退枪头失败")
        except Exception as e:
            self._notifications.warning(f"退枪头异常: {e}")
        finally:
            self.device_control_view.set_pipette_action_enabled(True)

    def initialize_body(self) -> None:
        """初始化身体（ModbusMotor）"""
        self._notifications.info("开始初始化身体...")

        try:
            self._services.devices.initialize(BODY_AXIS)
            self._render_device_state()
            self._notifications.info("身体初始化成功")
        except Exception as e:
            self._notifications.warning(
                f"身体初始化异常: {e}",
                modal=False,
            )
            self._render_device_state()

    def _collect_action_names(self) -> set[str]:
        """收集当前所有动作的名称（用于去重校验）"""
        names = set()
        for actions in self.actions.values():
            for a in actions:
                names.add(a.name)
        return names

    def _camera_choices(self, arm: str | None) -> list[tuple[str, str]]:
        """Expose relocalization cameras valid for the selected arm."""
        return list(
            self.settings.vision.camera_choices(
                CameraRole.RELOCALIZATION,
                arm=arm,
            )
        )

    def create_action(self) -> None:
        category = self.action_library_view.current_category_type()
        resolved = self._resolve_action_type_for_current_category(category)
        if resolved is None:
            return
        action_type, move_target = resolved if isinstance(resolved, tuple) else (resolved, None)

        if action_type == ActionType.TRAJECTORY:
            self.create_trajectory_action()
            return

        dialog = ActionConfigDialog(
            action_type,
            existing_names=self._collect_action_names(),
            initial_variant=move_target,
            pose_reader=self._read_current_arm_pose_for_form,
            localization_reader=self._services.external_localization.latest,
            station_choices_reader=self._services.vision.list_station_choices,
            camera_choices_reader=self._camera_choices,
        )
        if dialog.exec():
            action = dialog.get_action_definition()
            self._services.composition.create_action(
                action,
                origin="gui",
            )

    def create_trajectory_action(self) -> None:
        options = ["录制 R1", "录制 R2", "使用已有文件"]
        selected, ok = choose_item(
            self,
            "轨迹动作",
            "创建轨迹动作:",
            options,
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
            robot_options = ["R1", "R2"]
            robot_selected, robot_ok = choose_item(
                self,
                "轨迹执行机械臂",
                "选择执行轨迹的机械臂:",
                robot_options,
            )
            if not robot_ok:
                return
            robot_name = "robot2" if robot_selected == "R2" else "robot1"
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择轨迹文件",
                str(self._trajectory_dir(robot_name)),
                "轨迹文件 (*.txt);;所有文件 (*)"
            )
            if not file_path:
                return
            try:
                file_path = str(
                    self._services.trajectory_teaching.import_trajectory(
                        robot_name,
                        file_path,
                    )
                )
            except (OSError, ValueError) as exc:
                self._notifications.warning(f"轨迹文件导入失败: {exc}")
                return

        if not file_path:
            return

        default_name = f"{robot_name.upper()} {Path(file_path).stem}"
        name, name_ok = ask_text(
            self,
            "轨迹动作名称",
            "动作名称:",
            text=default_name,
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
        self._notifications.info(f"轨迹动作已创建: {name}")

    def delete_action(self) -> None:
        action_list = self.action_library_view.current_action_list()
        current_item = action_list.currentItem()
        if current_item is None:
            self._notifications.warning("请先选择一个要删除的动作")
            return

        action = current_item.data(Qt.ItemDataRole.UserRole)
        if action and action in self.actions[action.type]:
            self._services.composition.delete_action(
                action.id,
                origin="gui",
            )

    def edit_action(self) -> None:
        action_list = self._get_current_action_list_widget()
        if action_list is None:
            return

        current_item = action_list.currentItem()
        if current_item is None:
            self._notifications.warning("请先选择要修改的动作")
            return

        action = current_item.data(Qt.ItemDataRole.UserRole)
        if action is None:
            self._notifications.warning("无法读取选中的动作")
            return

        action_data = {
            "id": action.id,
            "name": action.name,
            "parameters": action.parameters
        }
        dialog = ActionConfigDialog(
            action.type,
            action_data,
            self,
            existing_names=self._collect_action_names(),
            pose_reader=self._read_current_arm_pose_for_form,
            localization_reader=self._services.external_localization.latest,
            station_choices_reader=self._services.vision.list_station_choices,
            camera_choices_reader=self._camera_choices,
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
            self._notifications.warning("未找到目标动作")
            return

    def refresh_action_list(self, action_type: ActionType) -> None:
        if action_type in {ActionType.MANIPULATE, ActionType.WAIT}:
            self._refresh_execute_merged_list()
            return

        # 移动类的所有子类型都显示在 move_list 中
        if action_type in {ActionType.MOVE, ActionType.BASE_MOVE}:
            self.action_library_view.action_list(ActionType.MOVE).clear()
            for action in self.actions[ActionType.MOVE]:
                self.action_library_view.action_list(ActionType.MOVE).add_action(action)
            for action in self.actions[ActionType.BASE_MOVE]:
                self.action_library_view.action_list(ActionType.MOVE).add_action(action)
            return

        if action_type in {ActionType.VISION_CAPTURE, ActionType.VISION_RELOCALIZE}:
            self.action_library_view.action_list(ActionType.VISION_CAPTURE).clear()
            for action in self.actions[ActionType.VISION_CAPTURE]:
                self.action_library_view.action_list(ActionType.VISION_CAPTURE).add_action(action)
            for action in self.actions[ActionType.VISION_RELOCALIZE]:
                self.action_library_view.action_list(ActionType.VISION_CAPTURE).add_action(action)
            return

        list_map = {
            ActionType.INSPECT: self.action_library_view.action_list(ActionType.INSPECT),
            ActionType.CHANGE_GUN: self.action_library_view.action_list(ActionType.CHANGE_GUN),
            ActionType.TRAJECTORY: self.action_library_view.action_list(ActionType.TRAJECTORY)
        }
        action_list = list_map[action_type]
        action_list.clear()

        for action in self.actions[action_type]:
            action_list.add_action(action)

    def load_actions(self) -> None:
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
            return
        if event.change_type is CompositionChangeType.SEQUENCE:
            if event.origin == "gui-canvas":
                return
            state = self._services.workflow_editing.snapshot()
            document = WorkflowDocument.from_entries(
                workflow_id=state.document.workflow_id,
                name=state.document.name,
                revision=state.document.revision,
                entries=self._services.composition.sequence_entries(),
                robot_profile_id=state.document.robot_profile_id,
            )
            self._services.workflow_editing.replace_document(document)
            self._render_sequence(document.to_entries())

    def _publish_current_sequence(self) -> None:
        state = self._services.workflow_editing.snapshot()
        document = WorkflowDocument.from_entries(
            workflow_id=state.document.workflow_id,
            name=state.document.name,
            revision=state.document.revision,
            entries=self.workflow_view.sequence_list.get_entries(),
            positions=state.document.position_map(),
            robot_profile_id=state.document.robot_profile_id,
        )
        self._services.workflow_editing.replace_document(document)

    def _render_sequence(
        self,
        entries: Sequence[SequenceEntry],
    ) -> None:
        self.workflow_view.sequence_list.render_entries(entries)

    def _insert_action_from_library(
        self,
        action: ActionDefinition,
    ) -> None:
        current_row = self.workflow_view.sequence_list.current_entry_row()
        insert_at = (
            self.workflow_view.sequence_list.entry_count()
            if current_row < 0
            else current_row + 1
        )
        self.workflow_view.sequence_list.insert_action(action, insert_at)

    def _choose_action_for_insertion(self, index: int) -> None:
        action = self._choose_action("插入动作")
        if action is not None:
            self.workflow_view.sequence_list.insert_action(action, index)

    def _choose_action_for_loop_insertion(
        self,
        loop_uuid: str,
        child_index: int,
    ) -> None:
        action = self._choose_action("向循环插入动作")
        if action is not None:
            self.workflow_view.sequence_list.insert_action_into_loop(
                loop_uuid,
                child_index,
                action,
            )

    def _choose_action_for_parallel_insertion(
        self,
        parallel_uuid: str,
        branch_id: str,
        child_index: int,
    ) -> None:
        action = self._choose_action("向并行分支插入动作")
        if action is not None:
            self.workflow_view.sequence_list.insert_action_into_parallel(
                parallel_uuid,
                branch_id,
                child_index,
                action,
            )

    def _choose_action_for_new_parallel_branch(self, parallel_uuid: str) -> None:
        action = self._choose_action("新增并行分支")
        if action is not None:
            self.workflow_view.sequence_list.add_parallel_branch(
                parallel_uuid,
                action,
            )

    def _choose_action(self, title: str) -> ActionDefinition | None:
        if not any(self.actions.values()):
            self._notifications.warning("动作库为空，请先创建动作")
            return None
        return ActionPickerDialog.choose(
            self.actions,
            title=title,
            parent=self,
        )

    def _refresh_execute_merged_list(self) -> None:
        self.action_library_view.action_list(ActionType.MANIPULATE).clear()
        for action in self.actions[ActionType.MANIPULATE]:
            self.action_library_view.action_list(ActionType.MANIPULATE).add_action(action)
        for action in self.actions[ActionType.WAIT]:
            self.action_library_view.action_list(ActionType.MANIPULATE).add_action(action)

    def _resolve_action_type_for_current_category(
        self,
        category: ActionType,
    ) -> ActionType | tuple[ActionType, str] | None:
        action_type_map = {
            ActionType.INSPECT: ActionType.INSPECT,
            ActionType.CHANGE_GUN: ActionType.CHANGE_GUN,
            ActionType.TRAJECTORY: ActionType.TRAJECTORY,
        }
        if category is ActionType.MANIPULATE:
            options = ["执行器动作", "等待"]
            selected, ok = choose_item(
                self,
                "选择动作类型",
                "在执行类下创建:",
                options,
            )
            if not ok:
                return None
            return ActionType.WAIT if selected == "等待" else ActionType.MANIPULATE
        
        # 移动类 Tab 需要选择具体类型
        if category is ActionType.MOVE:
            options = [
                "机械臂移动",
                "机械臂相对移动",
                "身体移动",
                "底盘移动",
            ]
            selected, ok = choose_item(
                self,
                "选择移动类型",
                "创建移动类动作:",
                options,
            )
            if not ok:
                return None
            if selected == "底盘移动":
                return ActionType.BASE_MOVE
            else:
                return (ActionType.MOVE, selected)

        if category is ActionType.VISION_CAPTURE:
            options = ["视觉抓取", "视觉重定位"]
            selected, ok = choose_item(
                self,
                "选择视觉动作",
                "创建视觉类动作:",
                options,
            )
            if not ok:
                return None
            return ActionType.VISION_RELOCALIZE if selected == "视觉重定位" else ActionType.VISION_CAPTURE

        return action_type_map.get(category)

    def _get_current_action_list_widget(self) -> ActionListWidget | None:
        return self.action_library_view.current_action_list()

    def refresh_task_library(self) -> None:
        self.task_library_view.task_library_list.clear()
        for summary in self._services.composition.list_tasks():
            task_name = summary.name
            display_name = _display_task_name(task_name)
            step_count = summary.step_count
            item = QListWidgetItem(f"{display_name} ({step_count} 步)")
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item.setSizeHint(QSize(100, 40))
            item.setIcon(self._create_task_list_icon())
            item.setToolTip(f"{display_name}\n步骤数: {step_count}\n拖到组合计划中")
            item.setData(Qt.ItemDataRole.UserRole, task_name)
            self.task_library_view.task_library_list.addItem(item)

    def _create_task_list_icon(self) -> QIcon:
        task_list = self.task_library_view.task_library_list
        return themed_icon(
            task_list,
            IconName.WORKFLOW,
            size=20,
            color=task_list.palette().highlight().color(),
        )

    def save_task(self) -> None:
        self._save_task(force_new_name=False)

    def save_task_as(self) -> None:
        self._save_task(force_new_name=True)

    def _save_task(self, *, force_new_name: bool) -> None:
        state = self._services.workflow_editing.snapshot()
        if not state.document.to_entries():
            self._notifications.warning("序列为空，无需保存")
            return
        try:
            if state.workflow_name and not force_new_name:
                stored_name, _ = self._services.workflow_editing.save()
            else:
                filename, _ = QFileDialog.getSaveFileName(
                    self,
                    "保存任务序列",
                    "",
                    "工作流文件 (*.workflow.json)",
                )
                if not filename:
                    return
                stored_name, _ = self._services.workflow_editing.save_as(
                    Path(filename).name
                )
        except CompositionRevisionConflict:
            self._notifications.warning("保存失败：该任务已被其他入口修改")
            return
        self._notifications.info(f"任务已保存: {stored_name}")

    def open_selected_task(self) -> None:
        task_name = self._selected_task_name()
        if task_name is None:
            return
        try:
            state = self._services.workflow_editing.open(task_name)
        except (FileNotFoundError, ValueError):
            self._notifications.warning(f"任务不存在: {task_name}")
            return
        self._render_sequence(state.document.to_entries())
        self._notifications.info(f"任务已打开: {task_name}")

    def insert_selected_task(self) -> None:
        task_name = self._selected_task_name()
        if task_name is None:
            return
        try:
            subworkflow = self._services.workflow_editing.instantiate(task_name)
        except (FileNotFoundError, ValueError):
            self._notifications.warning(f"任务不存在: {task_name}")
            return
        current_row = self.workflow_view.sequence_list.current_entry_row()
        insert_at = (
            self.workflow_view.sequence_list.entry_count()
            if current_row < 0
            else current_row + 1
        )
        self.workflow_view.sequence_list.insert_entry(subworkflow, insert_at)

    def _insert_subworkflow_by_name(self, task_name: str, index: int) -> None:
        subworkflow = self._instantiate_subworkflow(task_name)
        if subworkflow is None:
            return
        self.workflow_view.sequence_list.insert_entry(subworkflow, index)

    def _insert_subworkflow_into_loop_by_name(
        self,
        task_name: str,
        loop_uuid: str,
        child_index: int,
    ) -> None:
        subworkflow = self._instantiate_subworkflow(task_name)
        if subworkflow is None:
            return
        self.workflow_view.sequence_list.insert_subworkflow_into_loop(
            loop_uuid,
            child_index,
            subworkflow,
        )

    def _instantiate_subworkflow(self, task_name: str) -> SubworkflowBlock | None:
        try:
            return self._services.workflow_editing.instantiate(task_name)
        except (FileNotFoundError, ValueError):
            self._notifications.warning(f"任务不存在: {task_name}")
            return None

    def _selected_task_name(self) -> str | None:
        item = self.task_library_view.task_library_list.currentItem()
        if item is None:
            self._notifications.warning("请先选择一个已保存任务")
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def load_task(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "加载任务序列",
            str(self._services.composition.workflows_directory),
            "工作流文件 (*.workflow.json)",
        )
        if filename:
            item_name = Path(filename).name
            try:
                state = self._services.workflow_editing.open(item_name)
            except (FileNotFoundError, ValueError):
                self._notifications.warning(f"任务不存在: {item_name}")
                return
            self._render_sequence(state.document.to_entries())
            self._notifications.info(f"任务已加载: {item_name}")

    def start_execution(self) -> None:
        entries = self._services.workflow_editing.snapshot().document.to_entries()
        if not entries:
            self._notifications.warning("请先添加动作到序列中")
            return

        self._start_sequence_execution(
            entries,
            display_list=self.workflow_view.sequence_list,
            label="动作编排序列",
        )

    def execute_wake_welcome_workflow(self, workflow_name: str) -> None:
        """Execute the configured wake workflow without replacing the editor document."""
        if self._execution_view_model.snapshot().active:
            self._notifications.warning(
                f"跳过唤醒欢迎工作流，当前已有序列在执行: {workflow_name}",
                modal=False,
            )
            return

        try:
            entries = self._services.composition.load_task(workflow_name)
        except FileNotFoundError:
            entries = ()
        if not entries:
            self._notifications.warning(
                f"跳过唤醒欢迎工作流，工作流不存在或为空: {workflow_name}",
                modal=False,
            )
            return

        self._start_sequence_execution(entries, display_list=None, label="唤醒欢迎工作流")

    def _start_sequence_execution(
        self,
        sequence: Sequence[SequenceEntry],
        display_list: WorkflowCanvasWidget | None = None,
        label: str = "序列",
    ) -> None:
        if self._execution_view_model.snapshot().active:
            self._notifications.warning("当前已有序列正在执行")
            return

        if display_list is not None:
            try:
                state = self._services.workflow_editing.snapshot()
                document = WorkflowDocument.from_entries(
                    workflow_id=state.document.workflow_id,
                    name=state.document.name,
                    revision=state.document.revision,
                    entries=sequence,
                    robot_profile_id=state.document.robot_profile_id,
                )
                compiled = self._services.workflow_compiler.compile(document)
            except WorkflowCompilationError as exc:
                self._notifications.warning(
                    f"工作流校验失败：{exc}",
                    modal=False,
                )
                return
            preflight = self._services.workflow_preflight.check(compiled)
            if not preflight.ready:
                self._notifications.warning(
                    "执行前检查未通过：\n"
                    + "\n".join(issue.message for issue in preflight.issues),
                    modal=False,
                )
                return
            display_list.begin_execution(compiled)
            plan = compiled.plan
        else:
            entries = [
                entry
                for entry in sequence
                if isinstance(
                    entry,
                    (SequenceItem, LoopBlock, ParallelBlock, SubworkflowBlock),
                )
            ]
            plan = None

        self._notifications.info(f"开始执行{label}...")
        self._execution_display_list = display_list
        self._set_trajectory_buttons_enabled(False)
        self._pause_pose_refresh()

        submitted = (
            self._execution_bridge.execute_plan(plan, origin="gui")
            if plan is not None
            else self._execution_bridge.execute_sequence_items(entries, origin="gui")
        )
        if not submitted:
            if display_list is not None:
                display_list.finish_execution()
            self._set_trajectory_buttons_enabled(True)
            self._resume_pose_refresh()
            self._notifications.warning("提交执行失败")

    def toggle_pause(self) -> None:
        before = self._execution_view_model.snapshot()
        after = self._execution_view_model.toggle_pause()
        if after.state is before.state:
            return
        self._render_execution_state()
        self._notifications.info(
            "执行继续" if after.can_pause else "执行暂停"
        )

    def stop_execution(self) -> None:
        state = self._execution_view_model.snapshot()
        if state.can_cancel:
            self._execution_view_model.cancel()
            self._notifications.warning(
                "已发送任务停止请求（非硬件急停，将在当前动作可中断点停止）"
                , modal=False
            )
        else:
            self._notifications.info("当前没有正在执行的任务")

    def request_safety_stop(self, mode: StopMode) -> None:
        if not self._execution_bridge.request_safety_stop(mode):
            self._notifications.warning(
                "已有设备停止请求正在处理中",
                modal=False,
            )

    def on_execution_completed(self, success: bool) -> None:
        message = "序列执行成功" if success else "序列执行失败或已停止"
        if success:
            self._notifications.info(message)
        else:
            self._notifications.warning(message, modal=False)
        self._render_execution_state()
        display_list = self._execution_display_list
        if display_list is not None:
            display_list.finish_execution()
        self._execution_display_list = self.workflow_view.sequence_list
        self._set_trajectory_buttons_enabled(True)
        self._resume_pose_refresh()
        self.refresh_arm_poses()

    def _render_execution_state(self) -> None:
        state = self._execution_view_model.snapshot()
        self.workflow_view.render_execution_controls(
            state.pause_button_text,
            state.can_pause or state.can_resume,
            state.can_cancel,
        )

    def on_step_started(self, index: int, item: SequenceItem) -> None:
        display_list = self._execution_display_list
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_execution_step(index, item)

    def on_step_completed(self, index: int, item: SequenceItem) -> None:
        display_list = self._execution_display_list
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_execution_step(index, item)

    def on_step_failed(
        self,
        index: int,
        item: SequenceItem,
        error_msg: str,
    ) -> None:
        display_list = self._execution_display_list
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_execution_step(index, item)
        self._notifications.error(
            f"步骤 {index + 1} 失败:\n{error_msg}",
            title="执行失败",
        )

    def on_loop_progress(
        self,
        loop_uuid: str,
        current_iteration: int,
        total_iterations: int,
    ) -> None:
        del total_iterations
        display_list = self._execution_display_list
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_loop_progress(loop_uuid, current_iteration)

    def on_parallel_branch_state(
        self,
        parallel_uuid: str,
        branch_id: str,
        state: str,
        error: str,
    ) -> None:
        display_list = self._execution_display_list
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_parallel_branch_state(
                parallel_uuid,
                branch_id,
                state,
            )
        if error:
            self._notifications.error(
                f"并行分支执行失败：{error}",
                title="并行执行失败",
                modal=False,
            )

    def _ensure_canvas_execution_mapping(
        self,
        display_list: WorkflowCanvasWidget,
    ) -> None:
        if display_list.execution_mapping_active:
            return
        state = self._services.workflow_editing.snapshot()
        entries = state.document.to_entries()
        if not entries:
            return
        document = WorkflowDocument.from_entries(
            workflow_id=state.document.workflow_id,
            name=state.document.name,
            revision=state.document.revision,
            entries=entries,
            robot_profile_id=state.document.robot_profile_id,
        )
        try:
            compiled = self._services.workflow_compiler.compile(document)
        except WorkflowCompilationError:
            return
        display_list.begin_execution(compiled)

    def move_item_up(self) -> None:
        self.workflow_view.sequence_list.move_selected(-1)

    def move_item_down(self) -> None:
        self.workflow_view.sequence_list.move_selected(1)

    def delete_item(self) -> None:
        if not self.workflow_view.sequence_list.delete_selected():
            self._notifications.warning("请先选择要删除的节点")

    def repeat_sequence_selection(self) -> None:
        """将选中的连续动作包裹为 LoopBlock 循环容器"""
        rows = self.workflow_view.sequence_list.selected_entry_rows()
        if not rows:
            self._notifications.warning("请选择要循环的连续动作")
            return
        if rows != list(range(rows[0], rows[-1] + 1)):
            self._notifications.warning("只能循环连续选中的项目")
            return

        selected_loop = self.workflow_view.sequence_list.current_loop_block()
        initial_count = (
            selected_loop.repeat_count
            if len(rows) == 1 and selected_loop is not None
            else 2
        )

        repeat_count, ok = ask_integer(
            self,
            "循环执行",
            "循环次数 n:",
            initial_count,
            2,
            999,
            1,
        )
        if not ok or repeat_count <= 1:
            return

        if len(rows) == 1 and selected_loop is not None:
            self.workflow_view.sequence_list.update_current_loop_count(
                repeat_count
            )
            self._notifications.info(
                f"循环次数已更新为 {repeat_count}"
            )
            return

        try:
            total_steps = self.workflow_view.sequence_list.wrap_selected_in_loop(
                repeat_count
            )
        except ValueError as exc:
            self._notifications.warning(str(exc))
            return
        self._notifications.info(
            f"已创建循环块，共 {total_steps} 步"
        )

    def _selected_contiguous_rows(
        self,
        list_widget: QListWidget,
        empty_message: str,
    ) -> list[int] | None:
        rows = sorted(
            index.row() for index in list_widget.selectedIndexes()
        )
        if not rows:
            self._notifications.warning(empty_message)
            return None
        if rows != list(range(rows[0], rows[-1] + 1)):
            self._notifications.warning("只能循环连续选中的项目")
            return None
        return rows

    def edit_sequence_item(self) -> None:
        seq_item = self.workflow_view.sequence_list.current_sequence_item()
        if seq_item is None:
            loop = self.workflow_view.sequence_list.current_loop_block()
            if loop is None:
                self._notifications.warning("请先选择要修改的序列项")
                return
            repeat_count, accepted = ask_integer(
                self,
                "修改循环次数",
                "循环次数:",
                loop.repeat_count,
                2,
                999,
                1,
            )
            if accepted:
                self.workflow_view.sequence_list.update_current_loop_count(
                    repeat_count
                )
            return

        action_def = seq_item.definition
        action_data = {
            "id": action_def.id,
            "name": action_def.name,
            "parameters": action_def.parameters,
        }
        dialog = ActionConfigDialog(
            action_def.type,
            action_data,
            self,
            pose_reader=self._read_current_arm_pose_for_form,
            localization_reader=self._services.external_localization.latest,
            station_choices_reader=self._services.vision.list_station_choices,
            camera_choices_reader=self._camera_choices,
        )
        if not dialog.exec():
            return

        updated_definition = dialog.get_action_definition()
        self.workflow_view.sequence_list.update_current_action(
            updated_definition
        )
        self._notifications.info(f"已更新序列动作: {updated_definition.name}")

    def add_ai_sequence(
        self,
        sequence: Sequence[SequenceItem | dict[str, Any]],
        replace: bool = False,
        stagger_interval_ms: int = 0,
    ) -> None:
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
                self.workflow_view.sequence_list.render_entries(normalized)
                self._publish_current_sequence()
            else:
                for item in normalized:
                    self.workflow_view.sequence_list.insert_entry(item)
            self._notifications.info(
                f"已同步执行序列到右侧，共 {len(normalized)} 个动作"
            )
            return

        from PySide6.QtCore import QTimer

        if replace:
            self.workflow_view.sequence_list.render_entries(())
            self._publish_current_sequence()
        self._notifications.info(
            f"正在将 {len(normalized)} 个动作载入右侧序列区（逐项显示）..."
        )

        for i, item in enumerate(normalized):
            item.status = SequenceItemStatus.PENDING

            def make_add(seq_item: SequenceItem) -> Callable[[], None]:
                def _add() -> None:
                    self.workflow_view.sequence_list.insert_entry(seq_item)

                return _add

            QTimer.singleShot(stagger_interval_ms * i, make_add(item))

    def clear_sequence(self) -> None:
        if self._notifications.confirm("确定要清空所有序列吗？"):
            self.workflow_view.sequence_list.render_entries(())
            self._publish_current_sequence()
            self._notifications.info("序列已清空")

    def refresh_sequence_numbers(
        self,
        selected_row: int | None = None,
    ) -> None:
        """Keep the selected canvas node visible after external operations."""
        if selected_row is not None:
            self.workflow_view.sequence_list.set_current_entry_row(selected_row)

    def test_camera(self) -> None:
        """
        通过 DeviceRuntime 测试相机（与视觉抓取使用同一实例）。
        在独立 QThread 中运行，避免阻塞 UI。
        """
        self.action_library_view.set_camera_test_running(True)

        class _TestWorker(QThread):
            result = Signal(bool, str)

            def __init__(self, services: ApplicationServices):
                super().__init__()
                self._services = services

            def run(self) -> None:
                camera_name = (
                    self._services.settings.vision.camera_name_for_role(
                        CameraRole.VISION_CAPTURE
                    )
                    or None
                )
                session: CameraSession[CameraSource] | None = None

                try:
                    session = self._services.camera_access.open(
                        "gui-test"
                    )
                    mgr = session.camera

                    # 等待至少一路相机上线
                    deadline = time.time() + 10
                    online: list[dict[str, Any]] = []
                    while (
                        time.time() < deadline
                        and not self.isInterruptionRequested()
                    ):
                        info = mgr.get_cameras_info()
                        online = [c for c in info if c.get("online")]
                        if online:
                            break
                        time.sleep(0.3)
                    else:
                        if self.isInterruptionRequested():
                            return
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
                    while (
                        time.time() < deadline
                        and not self.isInterruptionRequested()
                    ):
                        if isinstance(mgr, DepthCameraSource):
                            frame = mgr.get_latest_depth_frame(camera_name)
                            if frame is not None:
                                height, width = frame.color_bgr.shape[:2]
                                center_depth_units = float(
                                    frame.depth_uint16[height // 2, width // 2]
                                )
                                center_distance_metres = (
                                    center_depth_units * frame.depth_scale_metres
                                )
                                serial_text = (
                                    f" SN={frame.camera_serial}"
                                    if frame.camera_serial
                                    else ""
                                )
                                message = (
                                    f"成功: 彩色={width}x{height}  "
                                    f"深度(中心)={center_distance_metres:.3f}m  "
                                    f"(相机={frame.camera_name}{serial_text})"
                                )
                                self.result.emit(True, message)
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

                    if self.isInterruptionRequested():
                        return
                    self.result.emit(False, "取帧超时（10 秒内未获得有效帧）")

                except Exception as e:
                    self.result.emit(False, f"测试异常: {str(e)}")
                finally:
                    if session is not None:
                        session.close()

        def on_result(success: bool, msg: str) -> None:
            message = f"[相机测试] {msg}"
            if success:
                self._notifications.info(message)
            else:
                self._notifications.warning(message, modal=False)
            self.action_library_view.set_camera_test_running(False)

        self._camera_test_thread = _TestWorker(self._services)
        self._camera_test_thread.result.connect(on_result)
        self._camera_test_thread.start()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.prepare_shutdown()
        event.accept()

    def prepare_shutdown(self) -> None:
        """Stop GUI producers before the event loop can accept late callbacks."""
        if self.startup_state is GuiStartupState.CLOSED:
            return
        begin_gui_shutdown("主窗口正在关闭")
        logger.info("应用正在关闭，后台资源将按顺序释放")
        self._notifications.begin_shutdown()
        self._notifications.info("应用正在关闭，后台资源将按顺序释放...")
        self._startup_begin_timer.stop()
        execution_state = self._execution_view_model.snapshot()
        if execution_state.active:
            self._execution_view_model.cancel()
        if self.pose_timer is not None:
            self.pose_timer.stop()
        camera_thread = self._camera_test_thread
        if camera_thread is not None and camera_thread.isRunning():
            camera_thread.requestInterruption()
        if self._hardware_startup_worker is not None:
            self._hardware_startup_worker.request_stop()
        self.ai_assistant_view.prepare_shutdown()
        self.workbench_view.persist_layout()
        self._composition_bridge.close()
        self._startup_lifecycle.close()

    def shutdown_after_event_loop(self) -> None:
        """Join GUI workers before their Qt owners and services are destroyed."""
        hardware_thread = self._hardware_startup_thread
        if hardware_thread is not None and hardware_thread.isRunning():
            if self._hardware_startup_worker is not None:
                self._hardware_startup_worker.request_stop()
            hardware_thread.quit()
            if not hardware_thread.wait(10_000):
                logger.warning(
                    "设备初始化线程未在 10 秒内退出；继续等待资源释放，"
                    "避免在线程运行时销毁 Qt 对象"
                )
                hardware_thread.wait()
        camera_thread = self._camera_test_thread
        if camera_thread is not None and camera_thread.isRunning():
            camera_thread.requestInterruption()
            if not camera_thread.wait(2000):
                logger.warning(
                    "相机测试线程未在 2 秒内退出；继续等待资源释放，"
                    "避免在线程运行时销毁 Qt 对象"
                )
                camera_thread.wait()
        self.ai_assistant_view.shutdown()
