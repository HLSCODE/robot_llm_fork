"""Qt adapter for shared AI dependencies and command approvals."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from ...application import (
    ApplicationServices,
    CommandRuntimeError,
    ExecutionControlAction,
)

if TYPE_CHECKING:
    from ..bridges.execution import ExecutionBridge

logger = logging.getLogger(__name__)


class AIController(QObject):
    """Expose the process command runtime through Qt signals."""

    status_changed = Signal(str)
    execution_started = Signal()
    sequence_execution_started = Signal(list)
    execution_finished = Signal(bool, str)
    error_occurred = Signal(str)

    def __init__(
        self,
        services: ApplicationServices,
        execution_bridge: ExecutionBridge,
    ) -> None:
        super().__init__()
        self._settings = services.settings
        self._services = services
        self._commands = services.commands
        self._execution_bridge = execution_bridge
        self._llm_registry = services.llm
        self._status_client = None
        execution_bridge.execution_completed.connect(
            self._on_execution_completed
        )

    def current_preview(self) -> dict[str, Any] | None:
        preview = self._commands.pending(expected_source="gui-ai")
        return preview.to_dict() if preview else None

    def confirm_and_execute(
        self,
        preview_id: str,
        version: int,
        *,
        risk_acknowledged: bool,
    ) -> bool:
        try:
            command = self._commands.confirm(
                preview_id,
                version,
                risk_acknowledged=risk_acknowledged,
                expected_source="gui-ai",
            )
        except CommandRuntimeError as exc:
            self.error_occurred.emit(str(exc))
            return False

        sequence = list(command.sequence)
        self.status_changed.emit("执行中...")
        self.execution_started.emit()
        self.sequence_execution_started.emit(sequence)
        accepted = self._execution_bridge.execute_sequence_items(
            sequence,
            origin=command.source,
        )
        if not accepted:
            self.execution_finished.emit(False, "执行提交失败")
            self.status_changed.emit("执行失败")
        return accepted

    def cancel_current_task(self) -> None:
        try:
            self._commands.control_execution(
                ExecutionControlAction.CANCEL,
                expected_source="gui-ai",
            )
        except Exception:
            logger.debug("no active execution to cancel", exc_info=True)
        self.status_changed.emit("已取消")

    def get_skill_list(self) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._commands.command_catalog()
            if entry.get("kind") == "skill"
        ]

    def is_llm_available(self) -> bool:
        client = self._get_status_client()
        return client is not None and client.is_available()

    def get_llm_model_name(self) -> str:
        client = self._get_status_client()
        return client.get_model_name() if client else "未配置"

    def get_model_provider(self) -> str:
        client = self._get_status_client()
        if client:
            return client.get_provider_name().upper()
        return self._settings.llm.llm_default_provider.upper()

    def is_api_key_set(self) -> bool:
        provider = self._settings.llm.llm_default_provider.lower()
        if provider == "minicpm":
            return bool(self._settings.llm.minicpm_gateway_host)
        keys = {
            "openai": self._settings.secrets.openai_api_key,
            "deepseek": self._settings.secrets.deepseek_api_key,
            "dashscope": self._settings.secrets.dashscope_api_key,
        }
        return bool(keys.get(provider, ""))

    def _get_status_client(self):
        if self._status_client is None:
            self._status_client = self._llm_registry.get_provider()
        return self._status_client

    def _on_execution_completed(self, success: bool) -> None:
        message = "执行成功" if success else "执行失败或已停止"
        self.execution_finished.emit(success, message)
        self.status_changed.emit("执行完成" if success else "执行失败")
