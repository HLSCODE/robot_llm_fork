"""
AI助手 Tab 组件
提供基于大模型的自然语言动作规划和执行功能
"""
import asyncio
import logging
from time import monotonic
from typing import Any, Dict

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..controllers.ai import AIController
from ...application import ApplicationServices
from ..bridges.execution import ExecutionBridge
from .dialogs import ActionPreviewDialog
from ...voice_interaction import (
    AudioOutputGate,
    CamerasModuleProvider,
    VoiceInteractionController,
    VoiceSessionState,
    WakeFeedback,
)
from ..controllers.audio import VoiceAudioPlayer
from ..theme import set_theme_role

logger = logging.getLogger(__name__)


INTERACTION_SHUTDOWN_TIMEOUT_SECONDS = 3.0


class VoiceSessionWorker(QObject):
    """Run one text turn through the interaction controller in a background thread."""

    event_ready = Signal(dict)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(
        self,
        controller: VoiceInteractionController,
        text: str,
        *,
        require_awake: bool = True,
    ):
        super().__init__()
        self._controller = controller
        self._text = text
        self._require_awake = require_awake

    @Slot()
    def run(self):
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self.finished.emit()

    async def _run_async(self):
        async for event in self._controller.handle_text(
            self._text,
            require_awake=self._require_awake,
        ):
            self.event_ready.emit(event.to_dict())


class VoiceSpeechRuntimeWorker(QObject):
    """Run real wake-word/ASR speech input in a background thread."""

    event_ready = Signal(dict)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(
        self,
        controller: VoiceInteractionController,
        voice_config: dict,
        audio_output_gate: AudioOutputGate,
    ):
        super().__init__()
        self._controller = controller
        self._voice_config = dict(voice_config)
        self._audio_output_gate = audio_output_gate
        self._runtime = None
        self._stop_requested = False

    @Slot()
    def run(self):
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self.finished.emit()

    @Slot()
    def stop(self):
        self._stop_requested = True
        if self._runtime is not None:
            self._runtime.stop()

    async def _run_async(self):
        from ..voice_interaction import build_voice_speech_runtime

        self._runtime = build_voice_speech_runtime(
            self._controller,
            self._voice_config,
            audio_output_gate=self._audio_output_gate,
        )
        wake_enabled = bool(self._voice_config.get("wake_word_enabled"))
        self.event_ready.emit({
            "type": "speech_runtime_started",
            "text": "真实语音监听已启动，等待唤醒词。" if wake_enabled else "真实语音监听已启动，请手动唤醒后开始说话。",
            "data": {
                "wake_word_enabled": wake_enabled,
                "asr_enabled": bool(self._voice_config.get("asr_enabled")),
            },
        })
        try:
            async for event in self._runtime.run():
                self.event_ready.emit(event.to_dict())
                if self._stop_requested:
                    break
        finally:
            self.event_ready.emit({
                "type": "speech_runtime_stopped",
                "text": "真实语音监听已停止",
            })


class AIAssistantWidget(QWidget):
    """
    AI助手 Tab 组件
    提供自然语言交互和动作序列预览功能
    """
    speech_runtime_startup_finished = Signal(bool)
    welcome_task_execution_requested = Signal(str)
    sequence_visualization_requested = Signal(object, bool, int)
    step_started = Signal(int, object)
    step_completed = Signal(int, object)
    step_failed = Signal(int, object, str)
    loop_progress = Signal(str, int, int)
    execution_completed = Signal(bool)

    def __init__(self, services: ApplicationServices, parent=None):
        super().__init__(parent)

        # 初始化执行桥接器和AI控制器
        self._services = services
        self._execution_bridge = ExecutionBridge(services)
        self._ai_controller = AIController(
            services,
            self._execution_bridge,
        )
        settings = services.settings
        self._voice_config = settings.voice.as_runtime_mapping()
        self._voice_controller = VoiceInteractionController(
            llm_registry=services.llm,
            command_runtime=services.commands,
            source="gui-ai",
            camera_provider=CamerasModuleProvider(
                session_factory=self._camera_capture_session,
                camera_name=settings.vision.vision_camera_name or None,
            ),
            timeout_s=self._voice_config["session_timeout_s"],
            turn_timeout_s=settings.runtime.interaction_turn_timeout_s,
            history_turns=self._voice_config["session_history_turns"],
            tts_enabled=self._voice_config["tts_enabled"],
            wake_feedback=WakeFeedback(
                enabled=bool(self._voice_config.get("wake_feedback_enabled", True)),
                text=str(self._voice_config.get("wake_feedback_text") or "明德博士在，请说。"),
            ),
        )
        self._voice_input_enabled = bool(
            self._voice_config.get("speech_input_enabled")
        )
        self._voice_thread = None
        self._voice_worker = None
        self._voice_event_source = "voice"
        self._speech_thread = None
        self._speech_worker = None
        self._speech_runtime_active = False
        self._speech_runtime_starting = False
        self._speech_runtime_startup_reported = False
        self._voice_processing = False
        self._voice_streaming_reply = False
        self._voice_audio_chunk_count = 0
        self._voice_audio_playback_error_reported = False
        self._voice_audio_output_gate = AudioOutputGate()
        self._voice_audio_player = VoiceAudioPlayer(
            self,
            output_gate=self._voice_audio_output_gate,
        )

        # 当前预览数据
        self._current_preview: Dict[str, Any] | None = None

        self._init_ui()
        self._connect_signals()
        self._update_status_display()
        self._update_voice_status_display()
        self._voice_timeout_timer = QTimer(self)
        self._voice_timeout_timer.setInterval(1000)
        self._voice_timeout_timer.timeout.connect(self._check_voice_session_timeout)
        self._voice_timeout_timer.start()

    def _camera_capture_session(self):
        return self._services.camera_access.open("gui-voice-capture")

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Status bar ──
        status_widget = QWidget()
        status_widget.setObjectName("aiStatusCard")
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(8, 2, 8, 2)

        self.status_label = QLabel("状态: 就绪")
        status_layout.addWidget(self.status_label)

        self.model_label = QLabel("模型: —")
        set_theme_role(self.model_label, "muted")
        status_layout.addWidget(self.model_label)

        status_layout.addStretch()

        self.simulation_checkbox = QCheckBox("模拟模式")
        self.simulation_checkbox.setChecked(self._services.simulation)
        self.simulation_checkbox.setEnabled(False)
        status_layout.addWidget(self.simulation_checkbox)

        layout.addWidget(status_widget)

        # ── Voice session status ──
        self.voice_group = QGroupBox("语音 Session")
        voice_layout = QHBoxLayout(self.voice_group)
        voice_layout.setContentsMargins(8, 6, 8, 6)
        voice_layout.setSpacing(6)

        self.voice_wake_button = QPushButton("唤醒")
        self.voice_wake_button.setMinimumHeight(30)
        set_theme_role(self.voice_wake_button, "success")
        self.voice_wake_button.clicked.connect(self._on_voice_wake_clicked)
        voice_layout.addWidget(self.voice_wake_button)
        self.voice_wake_button.setVisible(False)

        self.voice_sleep_button = QPushButton("结束语音会话")
        self.voice_sleep_button.setMinimumHeight(30)
        self.voice_sleep_button.clicked.connect(self._on_voice_sleep_clicked)
        voice_layout.addWidget(self.voice_sleep_button)

        self.voice_listen_button = QPushButton("启动监听")
        self.voice_listen_button.setMinimumHeight(30)
        set_theme_role(self.voice_listen_button, "primary")
        self.voice_listen_button.clicked.connect(self._on_voice_listen_clicked)
        voice_layout.addWidget(self.voice_listen_button)

        self.voice_state_label = QLabel("Session: 未唤醒")
        set_theme_role(self.voice_state_label, "muted")
        voice_layout.addWidget(self.voice_state_label)

        self.voice_asr_label = QLabel("监听: 未启动")
        set_theme_role(self.voice_asr_label, "muted")
        voice_layout.addWidget(self.voice_asr_label)

        self.voice_intent_label = QLabel("意图: —")
        set_theme_role(self.voice_intent_label, "muted")
        voice_layout.addWidget(self.voice_intent_label, stretch=1)

        self.voice_group.setVisible(self._voice_input_enabled)
        layout.addWidget(self.voice_group)

        # ── Chat history ──
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setMaximumHeight(220)
        self.chat_history.setPlaceholderText(
            "你好！我是 AI 动作助手。\n\n"
            "可以直接聊天、询问视觉信息，或输入要执行的动作，例如：\n"
            "• 帮我抓一个瓶子\n"
            "• 吸取 500 微升液体\n"
            "• 回到安全位置"
        )
        layout.addWidget(self.chat_history)

        # ── Skills ──
        skills_group = QGroupBox("⚙ 可用技能")
        skills_layout = QVBoxLayout(skills_group)
        skills_layout.setContentsMargins(4, 4, 4, 4)

        self.skill_list = QListWidget()
        self.skill_list.setMaximumHeight(90)
        skills_layout.addWidget(self.skill_list)
        self._refresh_skill_list()

        layout.addWidget(skills_group)

        # ── Input row ──
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入消息、问题或机器人指令，按 Enter 发送...")
        self.input_field.setMinimumHeight(34)
        self.input_field.returnPressed.connect(self._on_send_clicked)
        input_layout.addWidget(self.input_field, stretch=1)

        self.send_button = QPushButton("➤ 发送")
        self.send_button.setMinimumWidth(80)
        self.send_button.setMinimumHeight(34)
        set_theme_role(self.send_button, "primary")
        self.send_button.clicked.connect(self._on_send_clicked)
        input_layout.addWidget(self.send_button)

        layout.addLayout(input_layout)

        # ── Action buttons ──
        action_layout = QHBoxLayout()
        action_layout.setSpacing(6)

        self.execute_button = QPushButton("✅ 执行")
        self.execute_button.setEnabled(False)
        self.execute_button.setMinimumHeight(34)
        set_theme_role(self.execute_button, "success")
        self.execute_button.clicked.connect(self._on_execute_clicked)
        action_layout.addWidget(self.execute_button)

        self.preview_button = QPushButton("🔍 预览详情")
        self.preview_button.setEnabled(False)
        self.preview_button.setMinimumHeight(34)
        self.preview_button.clicked.connect(self._on_preview_clicked)
        action_layout.addWidget(self.preview_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setMinimumHeight(34)
        set_theme_role(self.cancel_button, "danger")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        action_layout.addWidget(self.cancel_button)

        layout.addLayout(action_layout)

        # Welcome
        self._add_bot_message("你好！我是 AI 动作助手。\n\n你可以直接输入消息、问题或机器人指令，不需要先唤醒。\n\n示例：\n• 帮我抓一个瓶子\n• 你现在看到了什么\n• 吸取 500 微升液体")

    def _connect_signals(self):
        """连接信号"""
        # AI 运行上下文只负责执行状态；对话/意图事件由 voice_interaction 返回。
        self._ai_controller.status_changed.connect(self._on_status_changed)
        self._ai_controller.error_occurred.connect(self._on_error_occurred)
        self._ai_controller.execution_started.connect(self._on_execution_started)
        self._ai_controller.sequence_execution_started.connect(self._on_sequence_execution_started)
        self._ai_controller.execution_finished.connect(self._on_execution_finished)

        # 执行桥接器信号
        self._execution_bridge.log_message.connect(self._on_execution_log)
        self._execution_bridge.execution_status_changed.connect(self._on_status_changed)
        self._execution_bridge.step_started.connect(self.step_started.emit)
        self._execution_bridge.step_completed.connect(self.step_completed.emit)
        self._execution_bridge.step_failed.connect(self.step_failed.emit)
        self._execution_bridge.loop_progress.connect(self.loop_progress.emit)
        self._execution_bridge.execution_completed.connect(
            self.execution_completed.emit
        )
        self._shutdown_prepared = False
        self._shutdown_complete = False
        self._voice_audio_player.error_occurred.connect(self._on_voice_audio_error)
        self._update_speech_runtime_controls()

    def _refresh_skill_list(self):
        """刷新技能列表"""
        self.skill_list.clear()
        skills = self._ai_controller.get_skill_list()
        for skill in skills:
            icon = skill.get("icon", "🤖")
            name = skill.get("name", "")
            category = skill.get("category", "")
            item_text = f"{icon} {name} ({category})"
            self.skill_list.addItem(item_text)

    def _add_user_message(self, text: str):
        """添加用户消息到对话历史"""
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Highlight)
        )
        cursor.insertText(f"\n👤 {text}\n\n")
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )
        self.chat_history.ensureCursorVisible()

    def _add_bot_message(self, text: str):
        """添加机器人消息到对话历史"""
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )
        cursor.insertText(f"\n🤖 {text}\n")
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )
        self.chat_history.ensureCursorVisible()

    def _add_system_message(self, text: str):
        """添加系统消息到对话历史"""
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(QColor("#d97706"))
        cursor.insertText(f"⚡ {text}\n\n")
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )
        self.chat_history.ensureCursorVisible()

        self.chat_history.ensureCursorVisible()

    def _update_status_display(self):
        """更新状态显示"""
        if not self._ai_controller.is_api_key_set():
            self.model_label.setText("模型: 未配置 🔴")
            set_theme_role(self.model_label, "danger")
        elif self._ai_controller.is_llm_available():
            provider = self._ai_controller.get_model_provider()
            model_name = self._ai_controller.get_llm_model_name()
            self.model_label.setText(f"模型: {provider} {model_name} 🟢")
            set_theme_role(self.model_label, "success")
        else:
            self.model_label.setText("模型: 连接失败 🔴")
            set_theme_role(self.model_label, "danger")

    def _set_input_enabled(self, enabled: bool):
        """设置输入控件的启用状态"""
        self.input_field.setEnabled(enabled)
        self.send_button.setEnabled(enabled)

    def _is_voice_session_active(self) -> bool:
        return self._voice_controller.session.state != VoiceSessionState.SLEEPING

    def _update_voice_status_display(self):
        if not hasattr(self, "voice_state_label"):
            return
        state = self._voice_controller.session.state
        state_text = {
            VoiceSessionState.SLEEPING: "未唤醒",
            VoiceSessionState.AWAKE: "已唤醒",
            VoiceSessionState.RESPONDING: "回复中",
            VoiceSessionState.PAUSED: "已暂停",
        }.get(state, state.value)
        self.voice_state_label.setText(f"Session: {state_text}")
        session_active = state != VoiceSessionState.SLEEPING
        if not session_active:
            set_theme_role(self.voice_state_label, "muted")
        else:
            set_theme_role(self.voice_state_label, "success")
        self.voice_sleep_button.setEnabled(session_active)
        self._update_speech_runtime_controls()

    def _update_speech_runtime_controls(self):
        if not hasattr(self, "voice_listen_button"):
            return
        if not self._voice_input_enabled:
            self.voice_group.setVisible(False)
            return
        self.voice_group.setVisible(True)

        if self._speech_runtime_starting:
            self.voice_listen_button.setText("加载中")
            self.voice_listen_button.setEnabled(False)
            self.voice_asr_label.setText("监听: 加载中")
            set_theme_role(self.voice_asr_label, "warning")
            return

        if self._speech_runtime_active:
            self.voice_listen_button.setText("停止监听")
            self.voice_listen_button.setEnabled(True)
            self.voice_asr_label.setText("监听: 运行中")
            set_theme_role(self.voice_asr_label, "success")
            return

        self.voice_listen_button.setText("启动监听")
        self.voice_listen_button.setEnabled(True)
        self.voice_asr_label.setText("监听: 待启动")
        set_theme_role(self.voice_asr_label, "muted")

    def _check_voice_session_timeout(self):
        if not self._voice_input_enabled:
            return
        if self._voice_processing:
            return
        event = self._voice_controller.check_timeout()
        if event is None:
            return
        self._handle_voice_event(event.to_dict())
        self.status_label.setText("状态: 会话超时")

    def _append_bot_delta(self, text: str):
        if not text:
            return
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )
        if not self._voice_streaming_reply:
            cursor.insertText("\n🤖 ")
            self._voice_streaming_reply = True
        cursor.insertText(text)
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )
        self.chat_history.ensureCursorVisible()

    def _finish_bot_delta(self):
        if not self._voice_streaming_reply:
            return
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("\n")
        self._voice_streaming_reply = False
        self.chat_history.ensureCursorVisible()

    def _enqueue_voice_audio(self, audio_data: str):
        if not audio_data:
            return
        if self._voice_audio_player.enqueue_base64(audio_data):
            self._voice_audio_chunk_count += 1

    # ==================== 事件处理 ====================

    def _on_send_clicked(self):
        """发送按钮点击"""
        text = self.input_field.text().strip()
        if not text:
            return

        # 检查API Key
        if not self._ai_controller.is_api_key_set():
            self._add_system_message("请先配置 LLM 模型！\n请在项目根目录创建 config.env 文件，设置 LLM_DEFAULT_PROVIDER 及对应模型配置。")
            return

        # 添加用户消息
        self._add_user_message(text)
        self.input_field.clear()

        self._start_dialog_text_turn(text)

    def _on_voice_wake_clicked(self):
        event = self._voice_controller.wake()
        self._handle_voice_event(event.to_dict())

    def _on_voice_sleep_clicked(self):
        event = self._voice_controller.sleep()
        self._handle_voice_event(event.to_dict())

    def _on_voice_listen_clicked(self):
        if self._speech_runtime_active or self._speech_runtime_starting:
            self._stop_voice_speech_runtime()
            return
        self._start_voice_speech_runtime()

    def start_voice_speech_runtime_if_configured(self) -> bool:
        """Auto-start real speech input when ASR and KWS are both enabled."""
        if not self._voice_config.get("speech_input_enabled"):
            return False
        return self._start_voice_speech_runtime(auto_start=True)

    def notify_speech_startup_wait_timeout(self):
        if not self._speech_runtime_starting:
            return
        timeout_s = self._voice_config.get(
            "speech_startup_wait_timeout_s", 30.0
        )
        self._add_system_message(
            f"语音模型加载超过 {float(timeout_s):.0f} 秒，先继续初始化机器人；语音监听会在后台继续加载。"
        )
        self.status_label.setText("状态: 语音后台加载中，继续初始化硬件...")

    def _start_voice_speech_runtime(self, auto_start: bool = False) -> bool:
        if self._speech_thread is not None:
            return True

        if not self._voice_config.get("speech_input_enabled"):
            self._add_system_message("真实语音输入未启用。请在 config.env 中设置 VOICE_INPUT_ENABLED=true，并重启 GUI 后再启动监听。")
            self._update_speech_runtime_controls()
            return False

        self._speech_runtime_starting = True
        self._speech_runtime_startup_reported = False
        self._update_speech_runtime_controls()
        self.status_label.setText("状态: 正在加载语音模型...")
        if auto_start:
            self._add_system_message("已启用 ASR 和唤醒词，正在自动加载真实语音监听链路...")
        else:
            self._add_system_message("正在加载真实语音监听链路...")

        self._speech_thread = QThread(self)
        self._speech_worker = VoiceSpeechRuntimeWorker(
            self._voice_controller,
            self._voice_config,
            self._voice_audio_output_gate,
        )
        self._speech_worker.moveToThread(self._speech_thread)
        self._speech_thread.started.connect(self._speech_worker.run)
        self._speech_worker.event_ready.connect(self._handle_voice_event)
        self._speech_worker.error_occurred.connect(self._on_speech_worker_error)
        self._speech_worker.finished.connect(self._on_speech_worker_finished)
        self._speech_worker.finished.connect(self._speech_thread.quit)
        self._speech_worker.finished.connect(self._speech_worker.deleteLater)
        self._speech_thread.finished.connect(self._on_speech_thread_finished)
        self._speech_thread.finished.connect(self._speech_thread.deleteLater)
        self._speech_thread.start()
        return True

    def _stop_voice_speech_runtime(self):
        if self._speech_worker is None:
            return
        self.voice_listen_button.setEnabled(False)
        self.voice_listen_button.setText("停止中")
        self.status_label.setText("状态: 正在停止语音监听...")
        self._speech_worker.stop()

    def _start_dialog_text_turn(self, text: str):
        if self._voice_processing:
            self._add_system_message("正在处理上一句话，请稍候")
            return

        self._clear_pending_preview()
        self._voice_processing = True
        self._voice_streaming_reply = False
        self._voice_audio_chunk_count = 0
        self._voice_audio_playback_error_reported = False
        self._voice_audio_player.stop()
        self._set_input_enabled(False)
        self.status_label.setText("状态: 对话处理中...")

        self._voice_thread = QThread(self)
        self._voice_worker = VoiceSessionWorker(
            self._voice_controller,
            text,
            require_awake=False,
        )
        self._voice_worker.moveToThread(self._voice_thread)
        self._voice_thread.started.connect(self._voice_worker.run)
        self._voice_worker.event_ready.connect(self._handle_dialog_event)
        self._voice_worker.error_occurred.connect(self._on_voice_worker_error)
        self._voice_worker.finished.connect(self._on_voice_worker_finished)
        self._voice_worker.finished.connect(self._voice_thread.quit)
        self._voice_worker.finished.connect(self._voice_worker.deleteLater)
        self._voice_thread.finished.connect(self._on_voice_thread_finished)
        self._voice_thread.finished.connect(self._voice_thread.deleteLater)
        self._voice_thread.start()

    def _clear_pending_preview(self) -> None:
        """Invalidate the previous preview before accepting new input."""
        self._services.commands.cancel_preview(
            expected_source="gui-ai"
        )
        self._current_preview = None
        self.preview_button.setEnabled(False)
        self.execute_button.setEnabled(False)

    @Slot(dict)
    def _handle_dialog_event(self, event: dict):
        self._handle_interaction_event(event, source="dialog")

    @Slot(dict)
    def _handle_voice_event(self, event: dict):
        self._handle_interaction_event(event, source="voice")

    def _handle_interaction_event(self, event: dict, *, source: str):
        event_type = event.get("type", "")
        is_voice_source = source == "voice"
        if event_type == "speech_runtime_started":
            self._speech_runtime_starting = False
            self._speech_runtime_active = True
            self._add_system_message(event.get("text") or "真实语音监听已启动")
            self.status_label.setText("状态: 语音监听中")
            self._update_speech_runtime_controls()
            if not self._speech_runtime_startup_reported:
                self._speech_runtime_startup_reported = True
                self.speech_runtime_startup_finished.emit(True)
        elif event_type == "speech_runtime_stopped":
            self._speech_runtime_starting = False
            self._speech_runtime_active = False
            self._voice_processing = False
            self._set_input_enabled(True)
            self._add_system_message(event.get("text") or "真实语音监听已停止")
            self.status_label.setText("状态: 就绪")
            self._update_speech_runtime_controls()
        elif event_type == "session_started":
            self._add_system_message(event.get("text") or ("语音会话已唤醒" if is_voice_source else "对话已开始"))
            if is_voice_source:
                self.voice_intent_label.setText("意图: —")
        elif event_type == "session_ended":
            preserve_audio = bool((event.get("data") or {}).get("preserve_audio"))
            self._finish_bot_delta()
            if not preserve_audio:
                self._voice_audio_player.stop()
            self._add_system_message(event.get("text") or ("语音会话已结束" if is_voice_source else "对话已结束"))
            if is_voice_source:
                self.voice_intent_label.setText("意图: —")
            self._voice_processing = False
            self._set_input_enabled(True)
        elif event_type == "session_paused":
            self._add_system_message(event.get("text") or ("语音会话已暂停" if is_voice_source else "对话已暂停"))
        elif event_type == "session_resumed":
            self._add_system_message(event.get("text") or ("语音会话已恢复" if is_voice_source else "对话已恢复"))
        elif event_type == "listening_started":
            self.status_label.setText("状态: 正在监听语音...")
        elif event_type == "speech_started":
            self.status_label.setText("状态: 检测到语音...")
        elif event_type == "asr_started":
            self.status_label.setText("状态: 语音识别中...")
        elif event_type == "asr_result":
            text = (event.get("text") or "").strip()
            elapsed_ms = float((event.get("data") or {}).get("elapsed_ms") or 0.0)
            if text:
                self._clear_pending_preview()
                self._voice_processing = True
                self._voice_streaming_reply = False
                self._voice_audio_chunk_count = 0
                self._voice_audio_playback_error_reported = False
                self._voice_audio_player.stop()
                self._set_input_enabled(False)
                self._add_user_message(text)
                self.status_label.setText(f"状态: ASR 识别完成 ({elapsed_ms:.0f} ms)")
            else:
                self._voice_processing = False
                self._set_input_enabled(True)
                self.status_label.setText("状态: 未识别到有效语音")
        elif event_type == "wake_welcome_requested":
            task_name = str((event.get("data") or {}).get("task_name") or "").strip()
            if not task_name:
                logger.debug("跳过唤醒欢迎动作: task=%s", task_name or "<empty>")
            else:
                self.welcome_task_execution_requested.emit(task_name)
        elif event_type == "ignored":
            self._add_system_message(event.get("text") or "已忽略")
            if self._speech_runtime_active and is_voice_source:
                self._voice_processing = False
                self._set_input_enabled(True)
                self.status_label.setText("状态: 语音监听中")
            elif not is_voice_source:
                self._voice_processing = False
                self._set_input_enabled(True)
                self.status_label.setText("状态: 就绪")
        elif event_type == "intent":
            intent = event.get("intent") or {}
            intent_name = intent.get("intent", "unknown")
            action = intent.get("session_action", "none")
            if is_voice_source:
                self.voice_intent_label.setText(f"意图: {intent_name} / {action}")
            self._add_system_message(f"意图: {intent_name}（session: {action}）")
        elif event_type == "text_delta":
            self._append_bot_delta(event.get("text_delta", ""))
        elif event_type == "audio_delta":
            self._enqueue_voice_audio(event.get("audio_data", ""))
        elif event_type == "vision_started":
            self._add_system_message(event.get("text") or "开始视觉观察")
        elif event_type == "command_preview":
            self._finish_bot_delta()
            data = event.get("data") or {}
            sequence = data.get("sequence") or []
            if (
                not data.get("preview_id")
                or not isinstance(data.get("version"), int)
                or not sequence
            ):
                self._current_preview = None
                self._add_system_message(
                    "动作预览缺少 ID、版本或动作序列"
                )
            else:
                self._current_preview = dict(data)
            if not data.get("suppress_message"):
                self._add_bot_message(event.get("text") or "已生成动作预览")
            self.preview_button.setEnabled(self._current_preview is not None)
            self.execute_button.setEnabled(self._current_preview is not None)
            self.cancel_button.setEnabled(True)
        elif event_type == "done":
            text = event.get("text", "")
            if text and not self._voice_streaming_reply:
                self._add_bot_message(text)
            if self._voice_audio_chunk_count == 0:
                self._enqueue_voice_audio(event.get("audio_data", ""))
            self._finish_bot_delta()
            if self._voice_audio_chunk_count:
                self._add_system_message(f"正在播放 {self._voice_audio_chunk_count} 段语音数据")
            if self._speech_runtime_active and is_voice_source:
                self._voice_processing = False
                self._set_input_enabled(True)
                self.status_label.setText("状态: 语音监听中")
            elif not is_voice_source:
                self._voice_processing = False
                self._set_input_enabled(True)
                self.status_label.setText("状态: 就绪")
        elif event_type == "error":
            self._finish_bot_delta()
            self._voice_audio_player.stop()
            self._voice_processing = False
            self._set_input_enabled(True)
            prefix = "语音会话错误" if is_voice_source else "对话错误"
            self._add_system_message(f"{prefix}: {event.get('text') or '未知错误'}")

        self._update_voice_status_display()

    @Slot(str)
    def _on_voice_worker_error(self, error: str):
        self._add_system_message(f"对话错误: {error}")
        self._voice_processing = False
        self._set_input_enabled(True)

    @Slot(str)
    def _on_speech_worker_error(self, error: str):
        was_starting = self._speech_runtime_starting
        self._speech_runtime_starting = False
        self._speech_runtime_active = False
        self._voice_processing = False
        self._set_input_enabled(True)
        self._update_speech_runtime_controls()
        self.status_label.setText("状态: 语音监听异常")
        self._add_system_message(f"真实语音监听错误: {error}")
        if was_starting and not self._speech_runtime_startup_reported:
            self._speech_runtime_startup_reported = True
            self.speech_runtime_startup_finished.emit(False)

    @Slot(str)
    def _on_voice_audio_error(self, error: str):
        if self._voice_audio_playback_error_reported:
            return
        self._voice_audio_playback_error_reported = True
        self._add_system_message(f"语音播放失败: {error}")

    @Slot()
    def _on_voice_worker_finished(self):
        self._finish_bot_delta()
        self._voice_processing = False
        self._set_input_enabled(True)
        self._update_voice_status_display()
        self.status_label.setText("状态: 就绪")

    @Slot()
    def _on_voice_thread_finished(self):
        self._voice_thread = None
        self._voice_worker = None

    @Slot()
    def _on_speech_worker_finished(self):
        was_starting = self._speech_runtime_starting
        self._speech_runtime_starting = False
        self._speech_runtime_active = False
        self._voice_processing = False
        self._set_input_enabled(True)
        self._update_speech_runtime_controls()
        if was_starting and not self._speech_runtime_startup_reported:
            self._speech_runtime_startup_reported = True
            self.speech_runtime_startup_finished.emit(False)

    @Slot()
    def _on_speech_thread_finished(self):
        self._speech_thread = None
        self._speech_worker = None

    def _on_execute_clicked(self):
        """执行按钮点击"""
        if self._current_preview is None:
            return
        risk = self._current_preview.get("risk") or {}
        if risk.get("requires_acknowledgement") is True:
            self._on_preview_clicked()
            return
        self._execute_preview(risk_acknowledged=False)

    def _execute_preview(self, *, risk_acknowledged: bool) -> None:
        if self._current_preview is None:
            return
        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        accepted = self._ai_controller.confirm_and_execute(
            str(self._current_preview["preview_id"]),
            int(self._current_preview["version"]),
            risk_acknowledged=risk_acknowledged,
        )
        if accepted:
            self._current_preview = None

    def _on_preview_clicked(self):
        """预览详情按钮点击"""
        if self._current_preview is None:
            return

        dialog = ActionPreviewDialog(
            items=self._current_preview.get("sequence") or [],
            skill_info=self._current_preview.get("skill_info") or {},
            risk=self._current_preview.get("risk") or {},
            parent=self
        )
        dialog.confirmed.connect(self._on_preview_confirmed)
        dialog.exec()

    def _on_preview_confirmed(self, risk_acknowledged: bool):
        """预览确认后执行"""
        self._execute_preview(risk_acknowledged=risk_acknowledged)

    def _on_cancel_clicked(self):
        """取消按钮点击"""
        self._voice_audio_player.stop()
        self._voice_controller.cancel_active_turn()
        self._ai_controller.cancel_current_task()
        self._reset_ui()

    # ==================== 执行上下文信号处理 ====================

    @Slot(str)
    def _on_status_changed(self, status: str):
        """状态变更"""
        self.status_label.setText(f"状态: {status}")

    @Slot(str)
    def _on_error_occurred(self, error: str):
        """错误发生"""
        self._add_system_message(f"错误: {error}")
        self._set_input_enabled(True)
        self._current_preview = self._ai_controller.current_preview()
        has_preview = self._current_preview is not None
        self.execute_button.setEnabled(has_preview)
        self.preview_button.setEnabled(has_preview)
        self.cancel_button.setEnabled(has_preview)

    @Slot()
    def _on_execution_started(self):
        """执行开始"""
        self._add_system_message("开始执行动作序列...")
        self.cancel_button.setEnabled(True)

    def _on_sequence_execution_started(self, sequence: list):
        """执行开始（携带序列数据，同步到右侧窗口）"""
        if sequence:
            self.sequence_visualization_requested.emit(sequence, True, 50)

    @Slot(bool, str)
    def _on_execution_finished(self, success: bool, message: str):
        """执行完成"""
        result = "成功" if success else "失败"
        self._add_bot_message(f"执行{result}: {message}")
        self._reset_ui()

    @Slot(str)
    def _on_execution_log(self, message: str):
        """执行日志"""
        # 在对话历史中显示执行日志
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.PlaceholderText)
        )
        cursor.insertText(f"  {message}\n")
        self.chat_history.setTextColor(
            self.palette().color(QPalette.ColorRole.Text)
        )

    def _reset_ui(self):
        """重置UI状态"""
        self._set_input_enabled(True)
        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self._current_preview = None

    @property
    def ai_controller(self) -> AIController:
        """获取AI控制器"""
        return self._ai_controller

    def prepare_shutdown(self) -> None:
        """Request interaction workers to stop without blocking the Qt GUI."""
        if self._shutdown_prepared:
            return
        self._shutdown_prepared = True
        if hasattr(self, "_voice_timeout_timer"):
            self._voice_timeout_timer.stop()
        self._voice_controller.cancel_active_turn()
        self._voice_audio_player.stop()
        if self._speech_worker is not None:
            self._speech_worker.stop()
        for thread in (self._voice_thread, self._speech_thread):
            if thread is not None and thread.isRunning():
                thread.quit()

    def shutdown(self) -> None:
        """Finish interaction cleanup within one shared timeout budget."""
        if self._shutdown_complete:
            return
        self.prepare_shutdown()
        deadline = monotonic() + INTERACTION_SHUTDOWN_TIMEOUT_SECONDS
        for thread in (self._voice_thread, self._speech_thread):
            if thread is None or not thread.isRunning():
                continue
            remaining_ms = max(0, int((deadline - monotonic()) * 1000))
            if remaining_ms == 0 or not thread.wait(remaining_ms):
                logger.error(
                    "交互线程未在关闭期限内退出: %s",
                    thread.objectName() or type(thread).__name__,
                )
        self._shutdown_complete = True
