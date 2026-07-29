"""AI runtime context for GUI execution.

User dialogue, intent classification, and command routing live in
``src.voice_interaction``. This module only owns shared runtime dependencies
and the execution state needed after voice_interaction has produced an action
preview.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..core.config_loader import Config
from ..core.models import SequenceItem
from ..llm import LLMRegistry
from ..skill_system import SkillEngine
from .execution_bridge import ExecutionBridge

logger = logging.getLogger(__name__)


class AIController(QObject):
    """Shared AI and execution context used by the GUI.

    This class intentionally does not parse natural language. The only
    conversation/intent entry is VoiceInteractionController.
    """

    status_changed = pyqtSignal(str)
    execution_started = pyqtSignal()
    sequence_execution_started = pyqtSignal(list)
    execution_finished = pyqtSignal(bool, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, execution_bridge: Optional[ExecutionBridge] = None):
        super().__init__()

        self._config = Config.get_instance()
        self._llm_registry: Optional[LLMRegistry] = None
        self._skill_engine: Optional[SkillEngine] = None
        self._execution_bridge = execution_bridge
        self._status_client = None

        self._current_sequence: List[SequenceItem] = []
        self._current_skill_info: Dict[str, Any] = {}
        self._current_preview_validated = False
        self._current_preview_confirmed = False
        if self._execution_bridge is not None:
            self._execution_bridge.execution_completed.connect(
                self._on_execution_completed
            )
        self._initialize()
        logger.info("AIController 初始化完成")

    def _initialize(self) -> None:
        """Initialize shared LLM and skill dependencies."""
        self._llm_registry = LLMRegistry.from_config(self._config)
        logger.info(
            "LLMRegistry 就绪: default=%s, providers=%s",
            self._llm_registry.default_provider,
            self._llm_registry.describe_providers(),
        )

        self._skill_engine = SkillEngine()
        skill_count = self._skill_engine.load_skills()
        logger.info("技能引擎加载了 %s 个技能", skill_count)

    def set_current_preview_from_dicts(
        self,
        items: List[Dict[str, Any]],
        skill_info: Dict[str, Any],
        *,
        validation_passed: bool,
        confirmation_required: bool,
    ) -> None:
        """Store a validated preview that must be explicitly confirmed."""
        self.clear_current_preview()
        if not validation_passed:
            raise ValueError("动作预览未通过校验")
        if not confirmation_required:
            raise ValueError("动作预览缺少显式确认要求")

        sequence = [SequenceItem.from_dict(item) for item in items]
        if not sequence:
            raise ValueError("动作预览不包含可执行动作")
        self._current_sequence = sequence
        self._current_skill_info = dict(skill_info or {})
        self._current_preview_validated = True

    def clear_current_preview(self) -> None:
        """Discard the current preview and all approval state."""
        self._current_sequence = []
        self._current_skill_info = {}
        self._current_preview_validated = False
        self._current_preview_confirmed = False

    def get_current_preview(self) -> tuple:
        """Return the currently stored action preview."""
        return self._current_sequence, self._current_skill_info

    def confirm_and_execute(self) -> None:
        """Execute the current action preview after user confirmation."""
        if not self._current_sequence:
            self.error_occurred.emit("没有可执行的动作序列")
            return
        if not self._current_preview_validated:
            self.error_occurred.emit("动作预览未通过校验，拒绝执行")
            return

        if self._execution_bridge is None:
            self.error_occurred.emit("执行器未初始化")
            return

        self._current_preview_confirmed = True
        self.status_changed.emit("执行中...")
        self.execution_started.emit()
        self.sequence_execution_started.emit(self._current_sequence)

        n = len(self._current_sequence)
        stagger_ms = 50
        delay_ms = min(stagger_ms * n + 180, 2000)

        logger.info(
            "开始执行动作序列，共 %s 个动作（%sms 后启动执行线程，供右侧动画）",
            n,
            delay_ms,
        )
        QTimer.singleShot(delay_ms, self._run_sequence_execution)

    def _run_sequence_execution(self) -> None:
        """Start real/simulated execution after the GUI preview animation."""
        if not self._current_sequence:
            return
        if not (
            self._current_preview_validated
            and self._current_preview_confirmed
        ):
            self.error_occurred.emit("动作预览未经校验和显式确认，拒绝执行")
            return
        if self._execution_bridge is None:
            self.error_occurred.emit("执行器未初始化")
            return

        self._current_preview_confirmed = False
        try:
            accepted = self._execution_bridge.execute_sequence_items(
                self._current_sequence,
                origin="ai",
            )

            if not accepted:
                self.execution_finished.emit(False, "执行失败")
                self.status_changed.emit("执行失败")
        except Exception as exc:
            logger.error("执行动作序列时发生错误: %s", exc, exc_info=True)
            self.error_occurred.emit(f"执行失败: {exc}")
            self.execution_finished.emit(False, str(exc))
            self.status_changed.emit("错误")

    def cancel_current_task(self) -> None:
        """Cancel current execution and clear the stored preview."""
        if self._execution_bridge:
            self._execution_bridge.stop_execution()

        self.clear_current_preview()

        self.status_changed.emit("已取消")
        logger.info("当前任务已取消")

    def get_skill_list(self) -> List[Dict[str, Any]]:
        if self._skill_engine is None:
            return []
        return self._skill_engine.list_all_skills()

    def get_llm_registry(self) -> Optional[LLMRegistry]:
        return self._llm_registry

    def get_skill_engine(self) -> Optional[SkillEngine]:
        return self._skill_engine

    def is_llm_available(self) -> bool:
        client = self._get_status_client()
        return client is not None and client.is_available()

    def get_llm_model_name(self) -> str:
        client = self._get_status_client()
        if client:
            return client.get_model_name()
        return "未配置"

    def get_model_provider(self) -> str:
        client = self._get_status_client()
        if client:
            return client.get_provider_name().upper()
        return self._config.LLM_DEFAULT_PROVIDER.upper()

    def is_api_key_set(self) -> bool:
        return Config.is_api_key_set()

    def _get_status_client(self):
        if self._llm_registry is None:
            return None
        if self._status_client is None:
            self._status_client = self._llm_registry.get_provider()
        return self._status_client

    def _on_execution_completed(self, success: bool) -> None:
        message = "执行成功" if success else "执行失败或已停止"
        self.clear_current_preview()
        self.execution_finished.emit(success, message)
        self.status_changed.emit("执行完成" if success else "执行失败")
