"""Frameless startup progress card shown before the operational window."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import ThemeMode, application_icon_for_mode
from ..branding import APPLICATION_NAME


class StartupProgressCard(QWidget):
    """Display bootstrap progress without exposing the unfinished main window."""

    exit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._progress = 0
        self.setObjectName("startupProgressWindow")
        self.setWindowTitle(f"{APPLICATION_NAME} - 正在启动")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(560)
        self._build_ui()
        self.adjustSize()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(18, 18, 18, 18)

        card = QFrame()
        card.setObjectName("startupCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(2, 6, 23, 150))
        card.setGraphicsEffect(shadow)
        outer_layout.addWidget(card)

        layout = QVBoxLayout(card)
        self._content_layout = layout
        layout.setContentsMargins(36, 32, 36, 28)
        layout.setSpacing(12)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(18)
        self.logo_label = QLabel()
        self.logo_label.setObjectName("startupLogo")
        self.logo_label.setFixedSize(68, 68)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setAccessibleName(f"{APPLICATION_NAME} Logo")
        self.logo_label.setPixmap(self._startup_icon().pixmap(QSize(50, 50)))
        brand_row.addWidget(self.logo_label, alignment=Qt.AlignmentFlag.AlignTop)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 2, 0, 0)
        brand_text.setSpacing(6)
        self.title_label = QLabel(APPLICATION_NAME)
        self.title_label.setObjectName("startupTitle")
        subtitle = QLabel("正在准备设备、语音与运行服务")
        subtitle.setObjectName("startupSubtitle")
        brand_text.addWidget(self.title_label)
        brand_text.addWidget(subtitle)
        brand_text.addStretch(1)
        brand_row.addLayout(brand_text, stretch=1)
        layout.addLayout(brand_row)

        accent = QFrame()
        accent.setObjectName("startupBrandAccent")
        accent.setFixedSize(44, 3)
        layout.addWidget(accent, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("startupProgressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        layout.addWidget(self.progress_bar)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 4, 0, 0)
        self.status_label = QLabel("正在创建应用服务...")
        self.status_label.setObjectName("startupStatus")
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("startupPercent")
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.percent_label)
        layout.addLayout(status_row)

        self.detail_label = QLabel("请稍候，初始化期间界面会保持响应")
        self.detail_label.setObjectName("startupDetail")
        self.detail_label.setWordWrap(False)
        layout.addWidget(self.detail_label)

        self.exit_button = QPushButton("退出")
        self.exit_button.setObjectName("startupExitButton")
        self.exit_button.setMinimumHeight(36)
        self.exit_button.hide()
        self.exit_button.clicked.connect(self.exit_requested)
        layout.addWidget(self.exit_button, alignment=Qt.AlignmentFlag.AlignRight)

        self.setStyleSheet(
            """
            QWidget#startupProgressWindow {
                background: transparent;
            }
            QFrame#startupCard {
                background: palette(base);
                border: 1px solid palette(mid);
                border-radius: 20px;
            }
            QLabel#startupLogo {
                background: transparent;
                border: none;
            }
            QFrame#startupBrandAccent {
                background: palette(highlight);
                border: none;
                border-radius: 1px;
            }
            QLabel#startupTitle {
                color: palette(text);
                font-size: 25px;
                font-weight: 700;
            }
            QLabel#startupSubtitle {
                color: palette(placeholder-text);
                font-size: 13px;
            }
            QProgressBar#startupProgressBar {
                background: palette(mid);
                border: none;
                border-radius: 4px;
            }
            QProgressBar#startupProgressBar::chunk {
                background: palette(highlight);
                border-radius: 4px;
            }
            QLabel#startupStatus {
                color: palette(highlight);
                font-size: 13px;
            }
            QLabel#startupPercent {
                color: palette(highlight);
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#startupDetail {
                color: palette(placeholder-text);
                font-size: 12px;
            }
            QPushButton#startupExitButton {
                color: #fecaca;
                background: #7f1d1d;
                border: 1px solid #991b1b;
                border-radius: 8px;
                padding: 6px 18px;
                font-weight: 600;
            }
            QPushButton#startupExitButton:hover {
                background: #991b1b;
            }
            """
        )
        self.detail_label.ensurePolished()
        self._detail_unbounded_max_height = self.detail_label.maximumHeight()
        self._normal_detail_height = self.detail_label.fontMetrics().lineSpacing() + 4
        self.detail_label.setFixedHeight(self._normal_detail_height)

    def _startup_icon(self) -> QIcon:
        palette = self.palette()
        mode = (
            ThemeMode.DARK
            if palette.window().color().lightnessF() < 0.5
            else ThemeMode.LIGHT
        )
        return application_icon_for_mode(mode)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center() - self.rect().center())

    def _fit_to_content(self) -> None:
        layout = self.layout()
        if layout is not None:
            self.setMinimumHeight(0)
            self.detail_label.updateGeometry()
            self.exit_button.updateGeometry()
            self._content_layout.invalidate()
            self._content_layout.activate()
            layout.invalidate()
            layout.activate()
        self.resize(self.width(), self.sizeHint().height())
        if self.isVisible():
            self._center_on_screen()

    def set_progress(
        self,
        percent: int,
        status: str,
        detail: str = "",
    ) -> None:
        self._progress = max(self._progress, min(100, max(0, int(percent))))
        self.progress_bar.setValue(self._progress)
        self.percent_label.setText(f"{self._progress}%")
        self.status_label.setText(status)
        visible_detail = detail.strip()
        self.detail_label.setText(visible_detail)
        self.detail_label.setToolTip(visible_detail)

    def mark_failed(self, message: str) -> None:
        self.status_label.setText("初始化失败")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(self._normal_detail_height)
        self.detail_label.setMaximumHeight(self._detail_unbounded_max_height)
        self.detail_label.setText(message)
        self.detail_label.setToolTip(message)
        self.detail_label.setStyleSheet("color: #fca5a5;")
        self.exit_button.show()
        self._fit_to_content()
