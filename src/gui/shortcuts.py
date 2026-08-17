"""Centralized, user-configurable application keyboard shortcuts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QKeySequenceEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from .app_dialogs import AppDialog, create_dialog_button_box, show_warning


SHORTCUT_SETTINGS_GROUP = "shortcuts"


@dataclass(frozen=True, slots=True)
class ShortcutDefinition:
    command_id: str
    label: str
    default_sequence: str


DEFAULT_SHORTCUTS: tuple[ShortcutDefinition, ...] = (
    ShortcutDefinition("file.save", "保存当前任务", "Ctrl+S"),
    ShortcutDefinition("file.save_as", "另存为任务", "Ctrl+Shift+S"),
    ShortcutDefinition("file.open", "加载任务", "Ctrl+O"),
    ShortcutDefinition("file.exit", "退出", "Ctrl+Q"),
    ShortcutDefinition("edit.undo", "撤销", "Ctrl+Z"),
    ShortcutDefinition("edit.redo", "重做", "Ctrl+Y"),
    ShortcutDefinition("edit.modify", "修改节点", "F2"),
    ShortcutDefinition("edit.delete", "删除节点", "Delete"),
    ShortcutDefinition("edit.clear", "清空工作流", "Ctrl+Shift+Delete"),
    ShortcutDefinition("view.sidebar", "切换资源侧栏", "Ctrl+B"),
    ShortcutDefinition("view.devices", "设备详情", "Ctrl+Alt+D"),
    ShortcutDefinition("view.poses", "机械臂位姿", "Ctrl+Alt+P"),
    ShortcutDefinition("view.controls", "基础控制", "Ctrl+Alt+C"),
    ShortcutDefinition("view.logs", "运行日志", "Ctrl+Alt+L"),
    ShortcutDefinition("view.theme_system", "跟随系统主题", "Ctrl+Alt+0"),
    ShortcutDefinition("view.theme_light", "浅色主题", "Ctrl+Alt+1"),
    ShortcutDefinition("view.theme_dark", "深色主题", "Ctrl+Alt+2"),
    ShortcutDefinition("view.reset_layout", "恢复默认布局", "Ctrl+Alt+R"),
    ShortcutDefinition("view.shortcuts", "快捷键设置", "Ctrl+Alt+K"),
    ShortcutDefinition("execution.start", "开始执行", "Ctrl+Return"),
    ShortcutDefinition("execution.pause", "暂停或恢复", "Ctrl+Space"),
    ShortcutDefinition("execution.stop", "停止任务", "Ctrl+Shift+X"),
    ShortcutDefinition("execution.quick_stop", "快速停止", "Ctrl+Alt+X"),
    ShortcutDefinition("execution.emergency", "设备急停", "Ctrl+Alt+Shift+X"),
    ShortcutDefinition("device.refresh_pose", "刷新机械臂位姿", "F5"),
    ShortcutDefinition("help.about", "关于软件", "F1"),
)


class ShortcutRegistry(QObject):
    """Own the command-to-key binding mapping and every registered QAction."""

    shortcuts_changed = Signal()

    def __init__(
        self,
        definitions: Iterable[ShortcutDefinition] = DEFAULT_SHORTCUTS,
        *,
        settings: QSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        definition_items = tuple(definitions)
        definitions_by_id = {
            definition.command_id: definition for definition in definition_items
        }
        if len(definitions_by_id) != len(definition_items):
            raise ValueError("shortcut command IDs must be unique")
        self._definitions = definitions_by_id
        self._settings = settings or QSettings("robot-llm", "robot-action-editor")
        self._actions: dict[str, QAction] = {}
        self._sequences = {
            command_id: self._load_sequence(definition)
            for command_id, definition in self._definitions.items()
        }

    @property
    def definitions(self) -> tuple[ShortcutDefinition, ...]:
        return tuple(self._definitions.values())

    def register(self, command_id: str, action: QAction) -> None:
        if command_id not in self._definitions:
            raise KeyError(f"unknown shortcut command: {command_id}")
        if command_id in self._actions:
            raise ValueError(f"shortcut command already registered: {command_id}")
        self._actions[command_id] = action
        action.setShortcut(self._sequences[command_id])

    def sequence(self, command_id: str) -> QKeySequence:
        return self._sequences[command_id]

    def set_sequences(self, sequences: Mapping[str, QKeySequence]) -> None:
        validated = self._validate_sequences(sequences)
        self._sequences = validated
        self._persist_sequences()
        for command_id, action in self._actions.items():
            action.setShortcut(self._sequences[command_id])
        self.shortcuts_changed.emit()

    def restore_defaults(self) -> None:
        self.set_sequences(
            {
                definition.command_id: QKeySequence(definition.default_sequence)
                for definition in self.definitions
            }
        )

    def open_editor(self, parent: QWidget) -> None:
        ShortcutSettingsDialog(self, parent).exec()

    def _load_sequence(self, definition: ShortcutDefinition) -> QKeySequence:
        self._settings.beginGroup(SHORTCUT_SETTINGS_GROUP)
        try:
            stored = self._settings.value(definition.command_id)
        finally:
            self._settings.endGroup()
        if isinstance(stored, str) and stored.strip():
            sequence = QKeySequence.fromString(
                stored,
                QKeySequence.SequenceFormat.PortableText,
            )
            if not sequence.isEmpty():
                return sequence
        return QKeySequence(definition.default_sequence)

    def _validate_sequences(
        self,
        sequences: Mapping[str, QKeySequence],
    ) -> dict[str, QKeySequence]:
        if set(sequences) != set(self._definitions):
            raise ValueError("快捷键设置不完整")
        normalized: dict[str, QKeySequence] = {}
        owner_by_sequence: dict[str, str] = {}
        for command_id, sequence in sequences.items():
            if sequence.isEmpty():
                raise ValueError(f"“{self._definitions[command_id].label}”必须设置快捷键")
            key = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            existing_owner = owner_by_sequence.get(key)
            if existing_owner is not None:
                existing_label = self._definitions[existing_owner].label
                current_label = self._definitions[command_id].label
                raise ValueError(f"“{existing_label}”与“{current_label}”使用了相同快捷键")
            owner_by_sequence[key] = command_id
            normalized[command_id] = sequence
        return normalized

    def _persist_sequences(self) -> None:
        self._settings.beginGroup(SHORTCUT_SETTINGS_GROUP)
        try:
            for command_id, sequence in self._sequences.items():
                self._settings.setValue(
                    command_id,
                    sequence.toString(QKeySequence.SequenceFormat.PortableText),
                )
        finally:
            self._settings.endGroup()


class ShortcutSettingsDialog(AppDialog):
    """Edit all registered shortcuts in one conflict-checked form."""

    def __init__(
        self,
        registry: ShortcutRegistry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self.setWindowTitle("快捷键设置")
        self.setMinimumWidth(420)

        layout = self.content_layout
        hint = QLabel("点击输入框后按下新的组合键。所有菜单命令都必须使用唯一快捷键。")
        hint.setWordWrap(True)
        hint.setProperty("themeRole", "muted")
        layout.addWidget(hint)

        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        self._editors: dict[str, QKeySequenceEdit] = {}
        for definition in registry.definitions:
            editor = QKeySequenceEdit(registry.sequence(definition.command_id))
            editor.setClearButtonEnabled(True)
            editor.setAccessibleName(f"{definition.label} 快捷键")
            self._editors[definition.command_id] = editor
            form.addRow(f"{definition.label}：", editor)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        buttons = create_dialog_button_box(
            self.content,
            accept_text="保存",
        )
        reset_button = QPushButton("恢复默认", buttons)
        buttons.addButton(
            reset_button,
            QDialogButtonBox.ButtonRole.ResetRole,
        )
        reset_button.clicked.connect(self._restore_defaults_in_form)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _restore_defaults_in_form(self) -> None:
        for definition in self._registry.definitions:
            self._editors[definition.command_id].setKeySequence(
                QKeySequence(definition.default_sequence)
            )

    def _apply(self) -> None:
        try:
            self._registry.set_sequences(
                {
                    command_id: editor.keySequence()
                    for command_id, editor in self._editors.items()
                }
            )
        except ValueError as error:
            show_warning(self, "快捷键设置", str(error))
            return
        self.accept()
