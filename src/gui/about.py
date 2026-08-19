"""Application identity and runtime details dialog."""

from __future__ import annotations

import platform
from collections.abc import Sequence

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .app_dialogs import AppDialog
from .branding import (
    APPLICATION_DESCRIPTION,
    APPLICATION_NAME,
    APPLICATION_PRODUCT_ID,
    APPLICATION_SUPPORTED_PLATFORMS,
    APPLICATION_VERSION,
)


def _create_information_section(
    parent: QWidget,
    title: str,
    entries: Sequence[tuple[str, str]],
    value_labels: dict[str, QLabel],
) -> QWidget:
    """Build one Ubuntu-style information column on the dialog background."""
    section = QWidget(parent)
    section.setObjectName("aboutInformationSection")
    section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(section)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(14)

    title_label = QLabel(title, section)
    title_label.setObjectName("aboutInformationSectionTitle")
    layout.addWidget(title_label)

    for label, value in entries:
        field = QWidget(section)
        field.setObjectName("aboutInformationField")
        field_layout = QVBoxLayout(field)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(2)

        name_label = QLabel(label, field)
        name_label.setObjectName("aboutRuntimeKey")
        value_label = QLabel(value, field)
        value_label.setObjectName("aboutRuntimeValue")
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        field_layout.addWidget(name_label)
        field_layout.addWidget(value_label)
        layout.addWidget(field)
        value_labels[label] = value_label

    layout.addStretch(1)
    return section


class AboutDialog(AppDialog):
    """Present stable product metadata and the active desktop runtime."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APPLICATION_NAME}")
        self.setMinimumWidth(600)

        identity_layout = QHBoxLayout()
        identity_layout.setContentsMargins(0, 0, 0, 4)
        identity_layout.setSpacing(14)
        self.logo_label = QLabel(self.content)
        self.logo_label.setObjectName("aboutApplicationIcon")
        self.logo_label.setFixedSize(56, 56)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        application = QApplication.instance()
        if application is not None and not application.windowIcon().isNull():
            self.logo_label.setPixmap(application.windowIcon().pixmap(44, 44))
        identity_layout.addWidget(self.logo_label)

        identity_text = QVBoxLayout()
        identity_text.setContentsMargins(0, 2, 0, 0)
        identity_text.setSpacing(4)
        self.name_label = QLabel(APPLICATION_NAME, self.content)
        self.name_label.setObjectName("aboutApplicationName")
        self.version_label = QLabel(
            f"版本 {APPLICATION_VERSION}",
            self.content,
        )
        self.version_label.setObjectName("aboutApplicationVersion")
        identity_text.addWidget(self.name_label)
        identity_text.addWidget(self.version_label)
        identity_layout.addLayout(identity_text, 1)
        self.content_layout.addLayout(identity_layout)

        self.description_label = QLabel(APPLICATION_DESCRIPTION, self.content)
        self.description_label.setObjectName("aboutApplicationDescription")
        self.description_label.setWordWrap(True)
        self.content_layout.addWidget(self.description_label)

        details = QFrame(self.content)
        details.setObjectName("aboutRuntimeDetails")
        details_layout = QHBoxLayout(details)
        details_layout.setContentsMargins(0, 6, 0, 4)
        details_layout.setSpacing(48)
        self.runtime_value_labels: dict[str, QLabel] = {}
        self.software_information_section = _create_information_section(
            details,
            "软件信息",
            (
                ("产品标识", APPLICATION_PRODUCT_ID),
                ("支持平台", APPLICATION_SUPPORTED_PLATFORMS),
                ("Python", platform.python_version()),
                ("Qt / PySide", f"{qVersion()} / {PYSIDE_VERSION}"),
            ),
            self.runtime_value_labels,
        )
        self.system_information_section = _create_information_section(
            details,
            "系统信息",
            (
                ("当前系统", f"{platform.system()} {platform.release()}"),
                ("系统架构", platform.machine() or "未知"),
            ),
            self.runtime_value_labels,
        )
        details_layout.addWidget(self.software_information_section, 1)
        details_layout.addWidget(self.system_information_section, 1)
        self.content_layout.addWidget(details)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close,
            parent=self.content,
        )
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        self.content_layout.addWidget(buttons)


def show_about_dialog(parent: QWidget) -> None:
    """Show the shared modal product-information dialog."""
    AboutDialog(parent).exec()
