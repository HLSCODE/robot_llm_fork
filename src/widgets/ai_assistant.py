"""
AI助手 Tab 组件
提供基于大模型的自然语言动作规划和执行功能
"""
import logging
from typing import List, Dict, Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QCheckBox, QScrollArea, QGroupBox,
    QListWidget, QListWidgetItem, QFrame
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont, QColor, QTextCursor

from ..ai_integration import AIController, ExecutionBridge
from ..gui.dialogs import ActionPreviewDialog

logger = logging.getLogger(__name__)


class AIAssistantWidget(QWidget):
    """
    AI助手 Tab 组件
    提供自然语言交互和动作序列预览功能
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # 初始化执行桥接器和AI控制器
        self._execution_bridge = ExecutionBridge()
        self._ai_controller = AIController(execution_bridge=self._execution_bridge)

        # 主窗口引用，用于同步动作序列到右侧
        self._main_window = None

        # 当前预览数据
        self._current_preview_items: List[Dict] = []
        self._current_skill_info: Dict = {}

        self._init_ui()
        self._connect_signals()
        self._update_status_display()

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Status bar ──
        status_widget = QWidget()
        status_widget.setStyleSheet("""
            QWidget { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 6px 10px; }
        """)
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(8, 2, 8, 2)

        self.status_label = QLabel("⚡ 状态: 就绪")
        self.status_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155; border: none; background: transparent;")
        status_layout.addWidget(self.status_label)

        self.model_label = QLabel("模型: —")
        self.model_label.setStyleSheet("font-size: 11px; color: #64748b; border: none; background: transparent;")
        status_layout.addWidget(self.model_label)

        status_layout.addStretch()

        self.simulation_checkbox = QCheckBox("模拟模式")
        self.simulation_checkbox.setChecked(False)
        self.simulation_checkbox.setStyleSheet("border: none; background: transparent;")
        self.simulation_checkbox.stateChanged.connect(self._on_simulation_changed)
        status_layout.addWidget(self.simulation_checkbox)

        layout.addWidget(status_widget)

        # ── Chat history ──
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setMaximumHeight(220)
        self.chat_history.setStyleSheet("""
            QTextEdit {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 10px;
                font-size: 12px;
                font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
            }
        """)
        self.chat_history.setPlaceholderText(
            "🤖 你好！我是 AI 动作助手。\n\n"
            "请输入您想要执行的动作，例如：\n"
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
        self.skill_list.setStyleSheet("""
            QListWidget {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                font-size: 11px;
            }
            QListWidget::item { padding: 4px 8px; }
            QListWidget::item:hover { background: #eff6ff; }
        """)
        skills_layout.addWidget(self.skill_list)
        self._refresh_skill_list()

        layout.addWidget(skills_group)

        # ── Input row ──
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("请输入您的指令，按 Enter 发送...")
        self.input_field.setMinimumHeight(34)
        self.input_field.setStyleSheet("""
            QLineEdit {
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 13px;
                background: #ffffff;
            }
            QLineEdit:focus { border-color: #3b82f6; background: #ffffff; }
        """)
        self.input_field.returnPressed.connect(self._on_send_clicked)
        input_layout.addWidget(self.input_field, stretch=1)

        self.send_button = QPushButton("➤ 发送")
        self.send_button.setMinimumWidth(80)
        self.send_button.setMinimumHeight(34)
        self.send_button.setStyleSheet("""
            QPushButton { background: #3b82f6; color: #fff; font-weight: 700; border: none; border-radius: 8px; font-size: 13px; }
            QPushButton:hover { background: #2563eb; }
            QPushButton:pressed { background: #1d4ed8; }
            QPushButton:disabled { background: #94a3b8; }
        """)
        self.send_button.clicked.connect(self._on_send_clicked)
        input_layout.addWidget(self.send_button)

        layout.addLayout(input_layout)

        # ── Action buttons ──
        action_layout = QHBoxLayout()
        action_layout.setSpacing(6)

        self.execute_button = QPushButton("✅ 执行")
        self.execute_button.setEnabled(False)
        self.execute_button.setMinimumHeight(34)
        self.execute_button.setStyleSheet("""
            QPushButton { background: #22c55e; color: #fff; font-weight: 700; border: none; border-radius: 8px; font-size: 13px; }
            QPushButton:hover { background: #16a34a; }
            QPushButton:pressed { background: #15803d; }
            QPushButton:disabled { background: #cbd5e1; color: #94a3b8; }
        """)
        self.execute_button.clicked.connect(self._on_execute_clicked)
        action_layout.addWidget(self.execute_button)

        self.preview_button = QPushButton("🔍 预览详情")
        self.preview_button.setEnabled(False)
        self.preview_button.setMinimumHeight(34)
        self.preview_button.setStyleSheet("""
            QPushButton { background: #ffffff; border: 1px solid #e2e8f0; color: #334155; font-weight: 500; border-radius: 8px; }
            QPushButton:hover { background: #f8fafc; border-color: #3b82f6; color: #3b82f6; }
            QPushButton:disabled { background: #f8fafc; color: #94a3b8; border-color: #e2e8f0; }
        """)
        self.preview_button.clicked.connect(self._on_preview_clicked)
        action_layout.addWidget(self.preview_button)

        self.cancel_button = QPushButton("✕ 取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setMinimumHeight(34)
        self.cancel_button.setStyleSheet("""
            QPushButton { background: #ffffff; border: 1px solid #e2e8f0; color: #ef4444; font-weight: 500; border-radius: 8px; }
            QPushButton:hover { background: #fef2f2; border-color: #ef4444; }
            QPushButton:disabled { background: #f8fafc; color: #94a3b8; border-color: #e2e8f0; }
        """)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        action_layout.addWidget(self.cancel_button)

        layout.addLayout(action_layout)

        # Welcome
        self._add_bot_message("你好！我是 AI 动作助手。\n\n请输入您想要执行的动作，我将帮您规划并执行。\n\n💡 示例：\n• 帮我抓一个瓶子\n• 吸取 500 微升液体\n• 回到安全位置")

    def set_main_window(self, main_window):
        """设置主窗口引用，并桥接执行信号到右侧序列列表（仅应调用一次）。"""
        self._main_window = main_window
        self._execution_bridge.set_main_window(main_window)
        self._execution_bridge.step_started.connect(main_window.on_step_started)
        self._execution_bridge.step_completed.connect(main_window.on_step_completed)
        self._execution_bridge.step_failed.connect(main_window.on_step_failed)
        self._execution_bridge.execution_completed.connect(main_window.on_execution_completed)

    def _connect_signals(self):
        """连接信号"""
        # AI控制器信号
        self._ai_controller.status_changed.connect(self._on_status_changed)
        self._ai_controller.error_occurred.connect(self._on_error_occurred)
        self._ai_controller.skill_matched.connect(self._on_skill_matched)
        self._ai_controller.skill_not_matched.connect(self._on_skill_not_matched)
        self._ai_controller.preview_ready.connect(self._on_preview_ready)
        self._ai_controller.execution_started.connect(self._on_execution_started)
        self._ai_controller.sequence_execution_started.connect(self._on_sequence_execution_started)
        self._ai_controller.execution_finished.connect(self._on_execution_finished)

        # 执行桥接器信号
        self._execution_bridge.log_message.connect(self._on_execution_log)
        self._execution_bridge.execution_status_changed.connect(self._on_status_changed)

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
        self.chat_history.setTextColor(QColor("#3b82f6"))
        cursor.insertText(f"\n👤 {text}\n\n")
        self.chat_history.setTextColor(QColor("#1e293b"))
        self.chat_history.ensureCursorVisible()

    def _add_bot_message(self, text: str):
        """添加机器人消息到对话历史"""
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(QColor("#334155"))
        cursor.insertText(f"\n🤖 {text}\n")
        self.chat_history.setTextColor(QColor("#1e293b"))
        self.chat_history.ensureCursorVisible()

    def _add_system_message(self, text: str):
        """添加系统消息到对话历史"""
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(QColor("#d97706"))
        cursor.insertText(f"⚡ {text}\n\n")
        self.chat_history.setTextColor(QColor("#1e293b"))
        self.chat_history.ensureCursorVisible()

        self.chat_history.ensureCursorVisible()

    def _update_status_display(self):
        """更新状态显示"""
        if not self._ai_controller.is_api_key_set():
            self.model_label.setText("模型: 未配置 🔴")
            self.model_label.setStyleSheet("font-size: 11px; color: #ef4444; font-weight: 600; border: none; background: transparent;")
        elif self._ai_controller.is_llm_available():
            provider = self._ai_controller.get_model_provider()
            model_name = self._ai_controller.get_llm_model_name()
            self.model_label.setText(f"模型: {provider} {model_name} 🟢")
            self.model_label.setStyleSheet("font-size: 11px; color: #16a34a; font-weight: 600; border: none; background: transparent;")
        else:
            self.model_label.setText("模型: 连接失败 🔴")
            self.model_label.setStyleSheet("font-size: 11px; color: #ef4444; font-weight: 600; border: none; background: transparent;")

    def _set_input_enabled(self, enabled: bool):
        """设置输入控件的启用状态"""
        self.input_field.setEnabled(enabled)
        self.send_button.setEnabled(enabled)

    # ==================== 事件处理 ====================

    def _on_send_clicked(self):
        """发送按钮点击"""
        text = self.input_field.text().strip()
        if not text:
            return

        # 检查API Key
        if not self._ai_controller.is_api_key_set():
            self._add_system_message("请先配置 LLM 模型！\n请在项目根目录创建 config.env 文件，设置 MODEL_PROVIDER 及对应模型配置。")
            return

        # 添加用户消息
        self._add_user_message(text)
        self.input_field.clear()

        # 处理输入
        self._set_input_enabled(False)
        self._ai_controller.process_input(text)

    def _on_execute_clicked(self):
        """执行按钮点击"""
        if not self._current_preview_items:
            return

        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._ai_controller.confirm_and_execute()

    def _on_preview_clicked(self):
        """预览详情按钮点击"""
        if not self._current_preview_items:
            return

        dialog = ActionPreviewDialog(
            items=self._current_preview_items,
            skill_info=self._current_skill_info,
            parent=self
        )
        dialog.confirmed.connect(self._on_preview_confirmed)
        dialog.exec()

    def _on_preview_confirmed(self):
        """预览确认后执行"""
        self._on_execute_clicked()

    def _on_cancel_clicked(self):
        """取消按钮点击"""
        self._ai_controller.cancel_current_task()
        self._reset_ui()

    def _on_simulation_changed(self, state: int):
        """模拟模式切换"""
        enabled = state == Qt.CheckState.Checked.value
        self._ai_controller.set_simulation_mode(enabled)
        mode_text = "启用" if enabled else "禁用"
        self._add_system_message(f"模拟模式: {mode_text}")

    # ==================== AI控制器信号处理 ====================

    @pyqtSlot(str)
    def _on_status_changed(self, status: str):
        """状态变更"""
        self.status_label.setText(f"状态: {status}")

    @pyqtSlot(str)
    def _on_error_occurred(self, error: str):
        """错误发生"""
        self._add_system_message(f"错误: {error}")
        self._set_input_enabled(True)
        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

    @pyqtSlot(str, dict)
    def _on_skill_matched(self, skill_id: str, params: dict):
        """技能匹配成功"""
        skill_info = self._current_skill_info
        skill_name = skill_info.get("name", skill_id)
        icon = skill_info.get("icon", "🤖")
        param_str = ", ".join([f"{k}={v}" for k, v in params.items()]) if params else "无参数"
        self._add_bot_message(f"已识别技能: {icon} {skill_name}\n参数: {param_str}")

    @pyqtSlot(str)
    def _on_skill_not_matched(self, error: str):
        """技能匹配失败"""
        self._add_bot_message(f"无法理解: {error}")
        self._set_input_enabled(True)

    @pyqtSlot(list, dict)
    def _on_preview_ready(self, items: list, skill_info: dict):
        """预览就绪"""
        self._current_preview_items = items
        self._current_skill_info = skill_info

        skill_name = skill_info.get("name", "")
        step_count = len(items)
        estimated_time = skill_info.get("estimated_time", 0)

        self._add_bot_message(f"技能 [{skill_name}] 已展开为 {step_count} 个动作\n预计执行时间: {estimated_time:.0f}秒")

        self.execute_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._set_input_enabled(True)

    @pyqtSlot()
    def _on_execution_started(self):
        """执行开始"""
        self._add_system_message("开始执行动作序列...")
        self.cancel_button.setEnabled(True)

    def _on_sequence_execution_started(self, sequence: list):
        """执行开始（携带序列数据，同步到右侧窗口）"""
        if self._main_window and sequence:
            # 与 AIController 中执行启动延迟一致，逐项「飞入」右侧卡片区
            self._main_window.add_ai_sequence(
                sequence, replace=True, stagger_interval_ms=50
            )

    @pyqtSlot(bool, str)
    def _on_execution_finished(self, success: bool, message: str):
        """执行完成"""
        result = "成功" if success else "失败"
        self._add_bot_message(f"执行{result}: {message}")
        self._reset_ui()

    @pyqtSlot(str)
    def _on_execution_log(self, message: str):
        """执行日志"""
        # 在对话历史中显示执行日志
        cursor = self.chat_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_history.setTextColor(QColor("#888"))
        cursor.insertText(f"  {message}\n")
        self.chat_history.setTextColor(QColor("#333"))

    def _reset_ui(self):
        """重置UI状态"""
        self._set_input_enabled(True)
        self.execute_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self._current_preview_items = []
        self._current_skill_info = {}

    @property
    def ai_controller(self) -> AIController:
        """获取AI控制器"""
        return self._ai_controller
