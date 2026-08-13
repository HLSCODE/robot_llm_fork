"""Cross-platform dialog chrome and semantic message dialogs."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .icons import IconName
from .theme import set_theme_role
from .toolbars import IconToolButton


class MessageDialogKind(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    QUESTION = "question"


class _DialogTitleBar(QWidget):
    """Own title display and native/fallback window movement."""

    def __init__(self, dialog: AppDialog) -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self._drag_offset: QPoint | None = None
        self.setObjectName("appDialogTitleBar")
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 6, 5)
        layout.setSpacing(8)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("appDialogApplicationIcon")
        self.icon_label.setFixedSize(22, 22)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("appDialogTitle")
        layout.addWidget(self.title_label, 1)
        self.close_button = IconToolButton(
            IconName.CLOSE,
            "关闭",
            callback=dialog.reject,
            parent=self,
            object_name="appDialogCloseButton",
            hit_size=30,
            icon_size=16,
        )
        layout.addWidget(self.close_button)

    def refresh(self) -> None:
        self.title_label.setText(self._dialog.windowTitle())
        icon = self._dialog.windowIcon()
        if icon.isNull():
            application = QApplication.instance()
            if application is not None:
                icon = application.windowIcon()
        self.icon_label.setVisible(not icon.isNull())
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(18, 18))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._drag_offset = (
            event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
        )
        handle = self._dialog.windowHandle()
        if handle is not None and handle.startSystemMove():
            self._drag_offset = None
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._dialog.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class AppDialog(QDialog):
    """A themed frameless dialog whose content remains ordinary Qt widgets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appDialogWindow")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        self.surface = QFrame(self)
        self.surface.setObjectName("appDialogSurface")
        root_layout.addWidget(self.surface)

        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(1, 1, 1, 1)
        surface_layout.setSpacing(0)
        self.title_bar = _DialogTitleBar(self)
        surface_layout.addWidget(self.title_bar)

        self.content = QWidget(self.surface)
        self.content.setObjectName("appDialogContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(18, 14, 18, 18)
        self.content_layout.setSpacing(12)
        surface_layout.addWidget(self.content, 1)

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        super().setWindowTitle(title)
        title_bar = getattr(self, "title_bar", None)
        if title_bar is not None:
            title_bar.refresh()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self.title_bar.refresh()
        self._center_over_parent()
        super().showEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            self.title_bar.refresh()

    def _center_over_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            center = parent.window().frameGeometry().center()
        else:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            center = screen.availableGeometry().center()
        self.move(center - self.rect().center())


def create_dialog_button_box(
    parent: QWidget,
    *,
    accept_text: str = "确定",
    reject_text: str = "取消",
) -> QDialogButtonBox:
    """Create consistently labelled confirmation buttons."""
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel,
        parent=parent,
    )
    accept_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
    reject_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    accept_button.setText(accept_text)
    reject_button.setText(reject_text)
    set_theme_role(accept_button, "primary")
    accept_button.setDefault(True)
    return buttons


class AppMessageDialog(AppDialog):
    """Present messages and confirmations without native platform chrome."""

    _SYMBOLS = {
        MessageDialogKind.INFO: "i",
        MessageDialogKind.WARNING: "!",
        MessageDialogKind.ERROR: "×",
        MessageDialogKind.CRITICAL: "!",
        MessageDialogKind.QUESTION: "?",
    }

    def __init__(
        self,
        kind: MessageDialogKind,
        title: str,
        message: str,
        *,
        parent: QWidget | None = None,
        confirm: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.setMaximumWidth(620)

        message_row = QHBoxLayout()
        message_row.setContentsMargins(0, 2, 0, 4)
        message_row.setSpacing(12)
        indicator = QLabel(self._SYMBOLS[kind], self.content)
        indicator.setObjectName("appMessageIndicator")
        indicator.setProperty("messageKind", kind.value)
        indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        indicator.setFixedSize(30, 30)
        indicator.setAccessibleName(kind.value)
        message_row.addWidget(indicator, alignment=Qt.AlignmentFlag.AlignTop)

        self.message_label = QLabel(message, self.content)
        self.message_label.setObjectName("appMessageText")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        message_row.addWidget(self.message_label, 1)
        self.content_layout.addLayout(message_row)

        if confirm:
            buttons = create_dialog_button_box(
                self.content,
                accept_text="确认",
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
        else:
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok,
                parent=self.content,
            )
            ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
            ok_button.setText("确定")
            set_theme_role(ok_button, "primary")
            ok_button.setDefault(True)
            buttons.accepted.connect(self.accept)
        self.content_layout.addWidget(buttons)


def show_message(
    parent: QWidget,
    kind: MessageDialogKind,
    title: str,
    message: str,
) -> None:
    AppMessageDialog(kind, title, message, parent=parent).exec()


def show_warning(parent: QWidget, title: str, message: str) -> None:
    show_message(parent, MessageDialogKind.WARNING, title, message)


def ask_confirmation(parent: QWidget, title: str, message: str) -> bool:
    dialog = AppMessageDialog(
        MessageDialogKind.QUESTION,
        title,
        message,
        parent=parent,
        confirm=True,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


def choose_item(
    parent: QWidget,
    title: str,
    label: str,
    items: list[str],
    *,
    current_index: int = 0,
) -> tuple[str, bool]:
    """Choose one item with the shared dialog chrome and button vocabulary."""
    if not items:
        raise ValueError("choice dialog items must not be empty")
    dialog = AppDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(320)
    prompt = QLabel(label, dialog.content)
    combo = QComboBox(dialog.content)
    combo.addItems(items)
    combo.setCurrentIndex(min(max(current_index, 0), len(items) - 1))
    combo.setAccessibleName(label.rstrip("：:"))
    dialog.content_layout.addWidget(prompt)
    dialog.content_layout.addWidget(combo)
    buttons = create_dialog_button_box(dialog.content)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    dialog.content_layout.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return combo.currentText(), accepted


def ask_text(
    parent: QWidget,
    title: str,
    label: str,
    *,
    text: str = "",
) -> tuple[str, bool]:
    """Read one text value using the shared dialog shell."""
    dialog = AppDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(380)
    prompt = QLabel(label, dialog.content)
    editor = QLineEdit(text, dialog.content)
    editor.setAccessibleName(label.rstrip("：:"))
    editor.selectAll()
    dialog.content_layout.addWidget(prompt)
    dialog.content_layout.addWidget(editor)
    buttons = create_dialog_button_box(dialog.content)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    dialog.content_layout.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return editor.text(), accepted


def ask_integer(
    parent: QWidget,
    title: str,
    label: str,
    value: int,
    minimum: int,
    maximum: int,
    step: int = 1,
) -> tuple[int, bool]:
    """Read one bounded integer using the shared dialog shell."""
    dialog = AppDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(320)
    prompt = QLabel(label, dialog.content)
    editor = QSpinBox(dialog.content)
    editor.setRange(minimum, maximum)
    editor.setSingleStep(step)
    editor.setValue(value)
    editor.setAccessibleName(label.rstrip("：:"))
    dialog.content_layout.addWidget(prompt)
    dialog.content_layout.addWidget(editor)
    buttons = create_dialog_button_box(dialog.content)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    dialog.content_layout.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return editor.value(), accepted
