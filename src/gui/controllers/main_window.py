import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import List
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..bridges.execution import ExecutionBridge
from ...application import (
    ApplicationServices,
    ComposedAction,
    ComposedTask,
    CompositionChangeType,
    CompositionEvent,
    CompositionRevisionConflict,
    WorkflowCompilationError,
)
from ...domain.models import (
    ActionDefinition,
    ActionType,
    LoopBlock,
    SequenceEntry,
    SequenceItem,
    SequenceItemStatus,
)
from ...domain.workflow import WorkflowDocument
from ...devices import StopMode
from ...devices.runtime.ids import (
    BODY_AXIS,
    MOBILE_BASE,
    PIPETTE,
    ROBOT_SYSTEM,
)
from ..views.log_widget import LogWidget
from ..bridges.composition import CompositionBridge
from ..views.device import DeviceControlView, DeviceStatusView
from ..views.dialogs import ActionConfigDialog
from ..views.action_list import ACTION_TYPE_LABELS
from ..views.action_picker import ActionPickerDialog
from ..bridges.notifications import GuiNotificationCenter
from .startup import (
    GuiHardwareStartupWorker,
    GuiStartupLifecycle,
    GuiStartupState,
    HardwareStartupStepResult,
)
from ..view_models.models import DeviceViewModel, ExecutionViewModel
from ..views.workflow import ActionLibraryView, WorkflowEditorView
from ..views.animated_drawer import AnimatedSplitterDrawer, DrawerHandleButton
from ..theme import ThemeController, ThemeMode


class MainWindow(QMainWindow):
    startup_progress_changed = Signal(int, str, str)
    startup_finished = Signal(bool, str)

    def __init__(
        self,
        services: ApplicationServices,
        theme_controller: ThemeController,
    ) -> None:
        super().__init__()
        self._services = services
        self._theme_controller = theme_controller
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
        self.robot_pose_cache = {"robot1": None, "robot2": None}
        self.pose_timer = None
        self._startup_lifecycle = GuiStartupLifecycle()
        self._speech_startup_wait_timer = None
        self._hardware_startup_thread = None
        self._hardware_startup_worker = None

        self.init_ui()
        self._notifications = GuiNotificationCenter(
            self,
            log_sink=self.log_widget.append_log,
            status_sink=lambda message: self.statusBar().showMessage(
                message,
                5000,
            ),
        )
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
        self.workflow_view.sequence_list.sequence_changed.connect(
            self._publish_current_sequence
        )
        self.load_actions()
        self._render_sequence(
            self._services.composition.sequence_entries()
        )
        self._execution_bridge.step_started.connect(self.on_step_started)
        self._execution_bridge.step_completed.connect(self.on_step_completed)
        self._execution_bridge.step_failed.connect(self.on_step_failed)
        self._execution_bridge.log_message.connect(self._notifications.info)
        self._execution_bridge.loop_progress.connect(self.on_loop_progress)
        self._execution_bridge.execution_completed.connect(
            self.on_execution_completed
        )
        self._execution_bridge.execution_status_changed.connect(
            lambda _status: self._render_execution_state()
        )
        self._render_execution_state()

        ai_assistant = self.action_library_view.ai_assistant
        if ai_assistant is not None:
            ai_assistant.speech_runtime_startup_finished.connect(
                self._on_speech_runtime_startup_finished
            )
            ai_assistant.welcome_task_execution_requested.connect(
                self.execute_wake_welcome_task
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
        QTimer.singleShot(0, self.start_startup_initialization)

    @property
    def startup_state(self) -> GuiStartupState:
        return self._startup_lifecycle.state

    def start_startup_initialization(self):
        """启动 GUI 显示前的必要初始化流程。"""
        if not self._startup_lifecycle.begin():
            return

        self.startup_progress_changed.emit(
            24,
            "正在准备语音与设备运行时...",
            "启动任务将在后台执行，主界面完成前保持隐藏",
        )

        try:
            speech_start_requested = False
            ai_assistant = self.action_library_view.ai_assistant
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
                "VOICE_INPUT_ENABLED=false",
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
            f"最多优先等待 {timeout_s:g} 秒，超时后语音继续后台加载",
        )

    def _on_speech_runtime_startup_finished(self, speech_ready: bool) -> None:
        if self.startup_state is not GuiStartupState.WAITING_FOR_SPEECH:
            return
        self.startup_progress_changed.emit(
            48,
            "语音监听已就绪" if speech_ready else "语音初始化不可用",
            "继续初始化设备" if speech_ready else "语音失败不阻止设备控制界面启动",
        )
        self.initialize_startup_hardware(speech_ready)

    def initialize_startup_hardware(self, _speech_ready: bool = False):
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

    def _on_speech_startup_wait_timeout(self):
        """Continue hardware startup if ASR/KWS first-load is still downloading."""
        if self.startup_state is not GuiStartupState.WAITING_FOR_SPEECH:
            return
        self.action_library_view.ai_assistant.notify_speech_startup_wait_timeout()
        self.startup_progress_changed.emit(
            44,
            "语音模型继续后台加载，开始初始化设备...",
            "语音就绪后会自动开始监听，不阻塞设备初始化",
        )
        self.initialize_startup_hardware(False)

    def _on_hardware_startup_step_started(self, device_id: str) -> None:
        progress = {
            ROBOT_SYSTEM: (54, "正在连接机械臂..."),
            MOBILE_BASE: (66, "正在连接移动底盘..."),
            BODY_AXIS: (76, "正在初始化身体控制器..."),
            PIPETTE: (88, "正在初始化移液枪..."),
        }
        percent, message = progress.get(device_id, (60, "正在初始化设备..."))
        self.startup_progress_changed.emit(percent, message, device_id)

    def _on_hardware_startup_step_completed(
        self,
        result: HardwareStartupStepResult,
    ) -> None:
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
            result.error or result.device_id,
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
        self.startup_progress_changed.emit(96, message, "正在启动附加服务...")
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

    def init_ui(self):
        self.setWindowTitle("机器人动作编排器")
        self.setMinimumSize(540, 800)
        self.resize(540, 960)

        self.create_menu()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.device_status_view = DeviceStatusView()
        layout.addWidget(self.device_status_view)

        # 底部：横向 Splitter，左=动作库，右=序列+控制+日志
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("workspaceSplitter")

        self.action_library_view = ActionLibraryView(self._services)
        self.workflow_view = WorkflowEditorView()
        self.device_control_view = DeviceControlView()
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(2, 2, 2, 2)
        right_layout.setSpacing(2)
        right_layout.addWidget(self.workflow_view, stretch=1)
        right_layout.addWidget(self.device_control_view)
        self.log_widget = LogWidget()
        right_layout.addWidget(self.log_widget)

        self._connect_view_signals()

        splitter.addWidget(self.action_library_view)
        splitter.addWidget(right_panel)
        self.action_library_toggle = DrawerHandleButton()
        drawer_handle = splitter.handle(1)
        drawer_handle_layout = QVBoxLayout(drawer_handle)
        drawer_handle_layout.setContentsMargins(0, 0, 0, 0)
        drawer_handle_layout.addWidget(self.action_library_toggle)
        splitter.setCollapsible(0, True)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((280, 560))
        self._action_library_drawer = AnimatedSplitterDrawer(
            splitter,
            self.action_library_toggle,
        )

        layout.addWidget(splitter, stretch=1)

        self.pose_timer = QTimer(self)
        self.pose_timer.setInterval(1000)
        self.pose_timer.timeout.connect(self.refresh_arm_poses)

    def _connect_view_signals(self) -> None:
        library = self.action_library_view
        library.create_requested.connect(self.create_action)
        library.edit_requested.connect(self.edit_action)
        library.delete_requested.connect(self.delete_action)
        library.camera_test_requested.connect(self.test_camera)
        library.task_add_requested.connect(self.add_task_to_composer)
        library.action_insert_requested.connect(
            self._insert_action_from_library
        )

        workflow = self.workflow_view
        workflow.start_requested.connect(self.start_execution)
        workflow.pause_requested.connect(self.toggle_pause)
        workflow.stop_requested.connect(self.stop_execution)
        workflow.safety_stop_requested.connect(self.request_safety_stop)
        workflow.move_up_requested.connect(self.move_item_up)
        workflow.move_down_requested.connect(self.move_item_down)
        workflow.edit_requested.connect(self.edit_sequence_item)
        workflow.repeat_requested.connect(self.repeat_sequence_selection)
        workflow.delete_requested.connect(self.delete_item)
        workflow.clear_requested.connect(self.clear_sequence)
        workflow.save_requested.connect(self.save_task)
        workflow.load_requested.connect(self.load_task)
        workflow.composer_remove_requested.connect(self.remove_task_from_composer)
        workflow.composer_move_up_requested.connect(self.move_composed_task_up)
        workflow.composer_move_down_requested.connect(self.move_composed_task_down)
        workflow.composer_repeat_requested.connect(self.repeat_composer_selection)
        workflow.composer_clear_requested.connect(self.clear_task_composer)
        workflow.composer_refresh_requested.connect(self.refresh_task_library)
        workflow.composer_add_requested.connect(self.add_task_to_composer)
        workflow.composer_execute_requested.connect(self.execute_composed_task)
        workflow.composer_save_requested.connect(self.save_composed_task)
        workflow.insert_action_at_requested.connect(
            self._choose_action_for_insertion
        )
        workflow.insert_action_in_loop_requested.connect(
            self._choose_action_for_loop_insertion
        )
        workflow.task_composer_list.task_dropped.connect(self._add_task_name_to_composer)
        workflow.task_composer_list.action_dropped.connect(self._add_action_to_composer)
        workflow.task_composer_list.order_changed.connect(self._move_composed_task_rows)

        device_status = self.device_status_view
        device_status.refresh_requested.connect(self.refresh_arm_poses)
        device_status.copy_pose_requested.connect(self.copy_robot_pose)
        controls = self.device_control_view
        controls.gripper_requested.connect(self._set_gripper_state)
        controls.relay_requested.connect(self._set_relay_state)
        controls.pipette_eject_requested.connect(self.eject_pipette_tip)

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

        view_menu = menubar.addMenu("视图")
        theme_menu = view_menu.addMenu("主题")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for label, mode in (
            ("跟随系统", ThemeMode.SYSTEM),
            ("浅色", ThemeMode.LIGHT),
            ("深色", ThemeMode.DARK),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(mode.value)
            action.setChecked(mode is self._theme_controller.mode)
            action.triggered.connect(
                lambda _checked=False, selected=mode: (
                    self._theme_controller.set_mode(selected)
                )
            )
            theme_group.addAction(action)
            theme_menu.addAction(action)
        self._theme_actions = {
            ThemeMode(action.data()): action
            for action in theme_group.actions()
        }
        self._theme_controller.mode_changed.connect(
            self._sync_theme_menu,
            Qt.ConnectionType.QueuedConnection,
        )

    def _sync_theme_menu(self, mode: object) -> None:
        if isinstance(mode, ThemeMode):
            self._theme_actions[mode].setChecked(True)

    def _set_relay_state(self, channel: int, turn_on: bool):
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

    def record_trajectory(self, robot_name: str):
        if not self._device_view_model.snapshot().robot_ready:
            self._notifications.warning(f"{robot_name.upper()} 未连接")
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
            self._notifications.info(f"{robot_name.upper()} 开始拖动示教")
            self._services.trajectory_teaching.start(robot_name)
            teaching_started = True

            self._notifications.info(
                f"{robot_name.upper()} 正在录制。请手动拖动机械臂，完成后点击确定停止并保存。",
                title="轨迹录制",
                modal=True,
            )

            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            save_result = self._services.trajectory_teaching.stop_and_save(
                filename
            )
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

    def run_trajectory(self, robot_name: str):
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
        self._notifications.info(message, title="轨迹", modal=True)

    def on_trajectory_failed(self, message: str):
        self._notifications.warning(message, title="轨迹")

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

    def _pause_pose_refresh(self):
        if self.pose_timer is not None and self.pose_timer.isActive():
            self.pose_timer.stop()

    def _resume_pose_refresh(self):
        if self.pose_timer is not None and not self.pose_timer.isActive():
            self.pose_timer.start()

    def refresh_arm_poses(self):
        self._refresh_single_robot_pose("robot1")
        self._refresh_single_robot_pose("robot2")
        self.refresh_external_localization()

    def refresh_external_localization(self):
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
                self.device_status_view.render_localization(text)
                return
        except Exception as exc:
            self.device_status_view.render_localization(f"UDP error: {exc}")
            return

        self.device_status_view.render_localization(
            self.format_external_localization_text(position)
        )

    def _refresh_single_robot_pose(self, robot_name: str):
        pose = self._get_current_pose(robot_name)
        if pose is None:
            self.robot_pose_cache[robot_name] = None
            self.device_status_view.render_pose(robot_name, "--")
            return

        self.robot_pose_cache[robot_name] = pose
        self.device_status_view.render_pose(robot_name, self.format_pose_text(pose))

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

    def format_external_localization_text(self, position: dict):
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
            self._notifications.warning(f"{robot_name.upper()} 位姿不可用")
            return

        pose_text = f"[{', '.join([f'{v:.6f}' for v in pose])}]"
        QApplication.clipboard().setText(pose_text)
        self._notifications.info(f"已复制 {robot_name.upper()} 位姿: {pose_text}")

    def initialize_robots(self):
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
        self.device_control_view.render_state(state)

    def initialize_pipette(self):
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

    def initialize_pipette_on_startup(self):
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

    def eject_pipette_tip(self):
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

    def initialize_body(self):
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

    def _collect_action_names(self) -> set:
        """收集当前所有动作的名称（用于去重校验）"""
        names = set()
        for actions in self.actions.values():
            for a in actions:
                names.add(a.name)
        return names

    def create_action(self):
        current_tab = self.action_library_view.action_tabs.currentIndex()
        resolved = self._resolve_action_type_for_current_tab(current_tab)
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
        self._notifications.info(f"轨迹动作已创建: {name}")

    def delete_action(self):
        current_tab = self.action_library_view.action_tabs.currentIndex()
        
        # 移动类 Tab 需要特殊处理，因为包含多种类型
        if current_tab == 0:
            current_item = self.action_library_view.action_list(ActionType.MOVE).currentItem()
            if current_item is None:
                self._notifications.warning("请先选择一个要删除的动作")
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
            ActionType.MANIPULATE: self.action_library_view.action_list(ActionType.MANIPULATE),
            ActionType.INSPECT: self.action_library_view.action_list(ActionType.INSPECT),
            ActionType.CHANGE_GUN: self.action_library_view.action_list(ActionType.CHANGE_GUN),
            ActionType.VISION_CAPTURE: self.action_library_view.action_list(ActionType.VISION_CAPTURE),
            ActionType.TRAJECTORY: self.action_library_view.action_list(ActionType.TRAJECTORY)
        }
        action_list = list_map[action_type]

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

    def edit_action(self):
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

    def refresh_action_list(self, action_type: ActionType):
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
            if event.origin == "gui-canvas":
                return
            self._render_sequence(
                self._services.composition.sequence_entries()
            )

    def _publish_current_sequence(self) -> None:
        try:
            self._services.composition.replace_sequence(
                self.workflow_view.sequence_list.get_entries(),
                origin="gui-canvas",
                expected_revision=self._sequence_revision,
            )
        except CompositionRevisionConflict:
            self._sequence_revision = (
                self._services.composition.sequence_revision
            )
            self._render_sequence(
                self._services.composition.sequence_entries()
            )
            self._notifications.warning(
                "序列已被其他入口修改，本次本地编辑未覆盖远程变更"
                , modal=False
            )
            return
        self._sequence_revision = (
            self._services.composition.sequence_revision
        )

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

    def _choose_action(self, title: str) -> ActionDefinition | None:
        if not any(self.actions.values()):
            self._notifications.warning("动作库为空，请先创建动作")
            return None
        return ActionPickerDialog.choose(
            self.actions,
            title=title,
            parent=self,
        )

    def _refresh_execute_merged_list(self):
        self.action_library_view.action_list(ActionType.MANIPULATE).clear()
        for action in self.actions[ActionType.MANIPULATE]:
            self.action_library_view.action_list(ActionType.MANIPULATE).add_action(action)
        for action in self.actions[ActionType.WAIT]:
            self.action_library_view.action_list(ActionType.MANIPULATE).add_action(action)

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
        current_tab = self.action_library_view.action_tabs.currentIndex()
        tab_list_map = {
            0: self.action_library_view.action_list(ActionType.MOVE),
            1: self.action_library_view.action_list(ActionType.MANIPULATE),
            2: self.action_library_view.action_list(ActionType.INSPECT),
            3: self.action_library_view.action_list(ActionType.CHANGE_GUN),
            4: self.action_library_view.action_list(ActionType.VISION_CAPTURE),
            6: self.action_library_view.action_list(ActionType.TRAJECTORY)
        }
        return tab_list_map.get(current_tab)

    def refresh_task_library(self):
        if not hasattr(self, "task_library_list"):
            return

        self.action_library_view.task_library_list.clear()
        for summary in self._services.composition.list_tasks():
            task_name = summary.name
            step_count = summary.step_count
            item = QListWidgetItem(f"{task_name} ({step_count} 步)")
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item.setSizeHint(QSize(100, 36))
            item.setIcon(self._create_task_list_icon())
            item.setToolTip(f"{task_name}\n步骤数: {step_count}\n拖到组合计划中")
            item.setData(Qt.ItemDataRole.UserRole, task_name)
            self.action_library_view.task_library_list.addItem(item)

    def _create_task_list_icon(self) -> QIcon:
        from PySide6.QtGui import QFont, QPainter, QPixmap

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
        from PySide6.QtCore import QRectF
        painter.drawText(QRectF(0, 0, 28, 28), Qt.AlignmentFlag.AlignCenter, "📋")
        painter.end()
        return QIcon(pixmap)

    def add_task_to_composer(self):
        current_item = self.action_library_view.task_library_list.currentItem()
        if current_item is None:
            self._notifications.warning("请先选择一个已保存任务")
            return

        task_name = current_item.data(Qt.ItemDataRole.UserRole)
        self._add_task_name_to_composer(task_name, self.workflow_view.task_composer_list.count())

    def _add_task_name_to_composer(self, task_name: str, insert_row: int | None = None):
        self._services.task_composer.add_task(task_name, index=insert_row)
        step_count = self._task_step_count(task_name)
        self._refresh_task_composer_display()

        if hasattr(self, "log_widget"):
            self._notifications.info(f"已加入任务组合: {task_name} ({step_count} 步)")

    def _add_action_to_composer(self, action: ActionDefinition, insert_row: int | None = None):
        self._services.task_composer.add_action(action, index=insert_row)
        self._refresh_task_composer_display()

        if hasattr(self, "log_widget"):
            self._notifications.info(f"已加入动作组合: {action.name}")

    def remove_task_from_composer(self):
        row = self.workflow_view.task_composer_list.currentRow()
        if row >= 0:
            self._services.task_composer.remove(row)
            self._refresh_task_composer_display()

    def move_composed_task_up(self):
        self._move_composed_task(-1)

    def move_composed_task_down(self):
        self._move_composed_task(1)

    def _move_composed_task(self, offset: int):
        current_row = self.workflow_view.task_composer_list.currentRow()
        target_row = current_row + offset
        if current_row < 0 or target_row < 0 or target_row >= self.workflow_view.task_composer_list.count():
            return

        self._services.task_composer.move(current_row, target_row)
        self._refresh_task_composer_display()
        self.workflow_view.task_composer_list.setCurrentRow(target_row)

    def _move_composed_task_rows(self, source_row: int, target_row: int) -> None:
        if source_row == target_row:
            return
        self._services.task_composer.move(source_row, target_row)
        self._refresh_task_composer_display()
        self.workflow_view.task_composer_list.setCurrentRow(target_row)

    def repeat_composer_selection(self):
        rows = self._selected_contiguous_rows(self.workflow_view.task_composer_list, "请选择要循环的连续任务或动作")
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

        start_row = rows[0]
        end_row = rows[-1]
        block_length = end_row - start_row + 1
        self._services.task_composer.repeat(start_row, end_row, repeat_count)
        self._refresh_task_composer_display()
        for row in range(start_row, start_row + block_length * repeat_count):
            self.workflow_view.task_composer_list.item(row).setSelected(True)
        self._notifications.info(f"组合块已设置为循环 {repeat_count} 次")

    def clear_task_composer(self):
        self._services.task_composer.clear()
        self._refresh_task_composer_display()

    def expand_composed_tasks(self, replace: bool):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            self._notifications.warning("请先向组合计划中添加至少一个任务")
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
        self._notifications.info(
            f"任务组合已{mode}到序列，共 {len(sequence)} 个动作"
        )

    def save_composed_task(self):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            self._notifications.warning("请先向组合计划中添加至少一个任务")
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
        self._notifications.info(f"组合任务已保存: {stored_name}")

    def _build_composed_task_sequence(self) -> list[SequenceItem]:
        return list(self._services.task_composer.build_sequence())

    def _refresh_task_composer_display(self):
        self.workflow_view.task_composer_list.clear()
        for entry in self._services.task_composer.entries():
            item = QListWidgetItem()
            if isinstance(entry, ComposedAction):
                action = entry.action
                item.setData(Qt.ItemDataRole.UserRole, True)
                item.setText(f"{action.name} (动作)")
                item.setIcon(self._create_action_card_icon(action))
                item.setToolTip(f"{action.name}\n类型: {action.type.value}\n拖动可调整顺序")
                self.workflow_view.task_composer_list.addItem(item)
                continue

            if not isinstance(entry, ComposedTask):
                raise TypeError(f"unsupported composer entry: {type(entry).__name__}")
            task_name = entry.task_name
            item.setData(Qt.ItemDataRole.UserRole, True)
            step_count = self._task_step_count(task_name)
            item.setText(f"{task_name} ({step_count} 步)")
            item.setIcon(self._create_task_card_icon(task_name, step_count, task_name))
            item.setToolTip(f"{task_name}\n步骤数: {step_count}\n拖动可调整顺序")
            self.workflow_view.task_composer_list.addItem(item)

    def _task_step_count(self, task_name: str) -> int:
        return self._services.task_composer.step_count(ComposedTask(task_name))

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

    def _create_task_card_icon(self, task_name: str, step_count: int, title: str | None = None):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

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
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

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
        type_label = ACTION_TYPE_LABELS.get(action.type, action.type.value)
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
        entries = self.workflow_view.sequence_list.get_entries()
        if not entries:
            self._notifications.warning("序列为空，无需保存")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "保存任务序列", "", "任务文件 (*.task)"
        )
        if filename:
            task_name = Path(filename).name
            stored_name = (
                self._services.composition.save_current_task(
                    task_name,
                    origin="gui",
                )
            )
            self._notifications.info(f"任务已保存: {stored_name}")

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
                self._notifications.warning(f"任务不存在: {task_name}")
                return
            self._notifications.info(f"任务已加载: {task_name}")

    def start_execution(self):
        entries = self._services.composition.sequence_entries()
        if not entries:
            self._notifications.warning("请先添加动作到序列中")
            return

        self._start_sequence_execution(
            entries,
            display_list=self.workflow_view.sequence_list,
            label="动作编排序列",
        )

    def execute_composed_task(self):
        sequence = self._build_composed_task_sequence()
        if not sequence:
            self._notifications.warning(
                "请先向组合计划中添加至少一个任务或动作"
            )
            return

        self._start_sequence_execution(sequence, display_list=None, label="任务组合序列")

    def execute_wake_welcome_task(self, task_name: str) -> None:
        """Execute a configured wake lifecycle task without affecting the composer."""
        if self._execution_view_model.snapshot().active:
            self._notifications.warning(
                f"跳过唤醒欢迎任务，当前已有序列在执行: {task_name}",
                modal=False,
            )
            return

        try:
            entries = self._services.composition.load_task(task_name)
        except FileNotFoundError:
            entries = ()
        if not entries:
            self._notifications.warning(
                f"跳过唤醒欢迎任务，任务不存在或为空: {task_name}",
                modal=False,
            )
            return

        self._start_sequence_execution(entries, display_list=None, label="唤醒欢迎任务")

    def _start_sequence_execution(
        self,
        sequence: Sequence[SequenceEntry],
        display_list=None,
        label: str = "序列",
    ):
        if self._execution_view_model.snapshot().active:
            self._notifications.warning("当前已有序列正在执行")
            return

        if display_list is not None:
            try:
                document = WorkflowDocument.from_entries(
                    workflow_id="current-sequence",
                    name="当前任务",
                    revision=self._sequence_revision,
                    entries=sequence,
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
            entries = list(compiled.entries)
            display_list.begin_execution(compiled)
        else:
            entries = [
                entry
                for entry in sequence
                if isinstance(entry, (SequenceItem, LoopBlock))
            ]

        self._notifications.info(f"开始执行{label}...")
        self._execution_display_list = display_list
        self._set_trajectory_buttons_enabled(False)
        self._pause_pose_refresh()

        if not self._execution_bridge.execute_sequence_items(
            entries,
            origin="gui",
        ):
            if display_list is not None:
                display_list.finish_execution()
            self._set_trajectory_buttons_enabled(True)
            self._resume_pose_refresh()
            self._notifications.warning("提交执行失败")

    def toggle_pause(self):
        before = self._execution_view_model.snapshot()
        after = self._execution_view_model.toggle_pause()
        if after.state is before.state:
            return
        self._render_execution_state()
        self._notifications.info(
            "执行继续" if after.can_pause else "执行暂停"
        )

    def stop_execution(self):
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

    def on_execution_completed(self, success: bool):
        message = "序列执行成功" if success else "序列执行失败或已停止"
        if success:
            self._notifications.info(message)
        else:
            self._notifications.warning(message, modal=False)
        self._render_execution_state()
        display_list = getattr(self, "_execution_display_list", None)
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

    def on_step_started(self, index: int, item: SequenceItem):
        display_list = getattr(self, "_execution_display_list", self.workflow_view.sequence_list)
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_execution_step(index, item)

    def on_step_completed(self, index: int, item: SequenceItem):
        display_list = getattr(self, "_execution_display_list", self.workflow_view.sequence_list)
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_execution_step(index, item)

    def on_step_failed(self, index: int, item: SequenceItem, error_msg: str):
        display_list = getattr(self, "_execution_display_list", self.workflow_view.sequence_list)
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_execution_step(index, item)
        self._notifications.error(
            f"步骤 {index + 1} 失败:\n{error_msg}",
            title="执行失败",
        )

    def on_loop_progress(self, loop_uuid: str, current_iteration: int, total_iterations: int):
        del total_iterations
        display_list = getattr(self, "_execution_display_list", None)
        if display_list is not None:
            self._ensure_canvas_execution_mapping(display_list)
            display_list.update_loop_progress(loop_uuid, current_iteration)

    def _ensure_canvas_execution_mapping(self, display_list) -> None:
        if display_list.execution_mapping_active:
            return
        entries = self._services.composition.sequence_entries()
        if not entries:
            return
        document = WorkflowDocument.from_entries(
            workflow_id="current-sequence",
            name="当前任务",
            revision=self._sequence_revision,
            entries=entries,
        )
        try:
            compiled = self._services.workflow_compiler.compile(document)
        except WorkflowCompilationError:
            return
        display_list.begin_execution(compiled)

    def move_item_up(self):
        self.workflow_view.sequence_list.move_selected(-1)

    def move_item_down(self):
        self.workflow_view.sequence_list.move_selected(1)

    def delete_item(self):
        if not self.workflow_view.sequence_list.delete_selected():
            self._notifications.warning("请先选择要删除的节点")

    def repeat_sequence_selection(self):
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

        repeat_count, ok = QInputDialog.getInt(
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
        list_widget,
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

    def edit_sequence_item(self):
        seq_item = self.workflow_view.sequence_list.current_sequence_item()
        if seq_item is None:
            loop = self.workflow_view.sequence_list.current_loop_block()
            if loop is None:
                self._notifications.warning("请先选择要修改的序列项")
                return
            repeat_count, accepted = QInputDialog.getInt(
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
            self._notifications.info(
                f"已同步执行序列到右侧，共 {len(normalized)} 个动作"
            )
            return

        from PySide6.QtCore import QTimer

        if replace:
            self._services.composition.clear_sequence(
                origin="gui-ai",
            )
        self._notifications.info(
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
        if self._notifications.confirm("确定要清空所有序列吗？"):
            self._services.composition.clear_sequence(
                origin="gui",
            )
            self._notifications.info("序列已清空")

    def refresh_sequence_numbers(self, selected_row: int | None = None):
        """Keep the selected canvas node visible after external operations."""
        if selected_row is not None:
            self.workflow_view.sequence_list.set_current_entry_row(selected_row)

    def test_camera(self):
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

                    if self.isInterruptionRequested():
                        return
                    self.result.emit(False, "取帧超时（10 秒内未获得有效帧）")

                except Exception as e:
                    self.result.emit(False, f"测试异常: {str(e)}")
                finally:
                    if session is not None:
                        session.close()

        def on_result(success, msg):
            message = f"[相机测试] {msg}"
            if success:
                self._notifications.info(message)
            else:
                self._notifications.warning(message, modal=False)
            self.action_library_view.set_camera_test_running(False)

        self._camera_test_thread = _TestWorker(self._services)
        self._camera_test_thread.result.connect(on_result)
        self._camera_test_thread.start()

    def closeEvent(self, event):
        execution_state = self._execution_view_model.snapshot()
        if execution_state.active:
            self._execution_view_model.cancel()
        self._notifications.info("应用正在关闭，后台资源将按顺序释放...")
        if self.pose_timer is not None:
            self.pose_timer.stop()
        camera_thread = getattr(self, "_camera_test_thread", None)
        if camera_thread is not None and camera_thread.isRunning():
            camera_thread.requestInterruption()
        if self._hardware_startup_worker is not None:
            self._hardware_startup_worker.request_stop()
        self.action_library_view.ai_assistant.prepare_shutdown()
        self._composition_bridge.close()
        self._startup_lifecycle.close()
        event.accept()

    def shutdown_after_event_loop(self) -> None:
        """Finish bounded worker cleanup after the visible GUI has closed."""
        hardware_thread = self._hardware_startup_thread
        if hardware_thread is not None and hardware_thread.isRunning():
            if self._hardware_startup_worker is not None:
                self._hardware_startup_worker.request_stop()
            hardware_thread.quit()
            if not hardware_thread.wait(10_000):
                self._notifications.warning(
                    "设备初始化线程未在 10 秒内退出，设备关闭流程将等待资源释放",
                    modal=False,
                )
        camera_thread = getattr(self, "_camera_test_thread", None)
        if camera_thread is not None and camera_thread.isRunning():
            camera_thread.requestInterruption()
            if not camera_thread.wait(2000):
                self._notifications.warning(
                    "相机测试线程未在 2 秒内退出，将由设备关闭流程继续清理",
                    modal=False,
                )
        self.action_library_view.ai_assistant.shutdown()
