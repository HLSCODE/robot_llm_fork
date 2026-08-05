"""Frameless startup progress card shown before the operational window."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
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


class StartupProgressCard(QWidget):
    """Display bootstrap progress without exposing the unfinished main window."""

    exit_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._progress = 0
        self.setObjectName("startupProgressWindow")
        self.setWindowTitle("机器人动作编排器 - 正在启动")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 300)
        self._build_ui()

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
        layout.setContentsMargins(36, 32, 36, 28)
        layout.setSpacing(12)

        title = QLabel("机器人动作编排器")
        title.setObjectName("startupTitle")
        subtitle = QLabel("正在准备设备、语音与运行服务")
        subtitle.setObjectName("startupSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(20)

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
        self.detail_label.setWordWrap(True)
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
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 16px;
            }
            QLabel#startupTitle {
                color: #f8fafc;
                font-size: 25px;
                font-weight: 700;
            }
            QLabel#startupSubtitle {
                color: #94a3b8;
                font-size: 13px;
            }
            QProgressBar#startupProgressBar {
                background: #334155;
                border: none;
                border-radius: 4px;
            }
            QProgressBar#startupProgressBar::chunk {
                background: #34d399;
                border-radius: 4px;
            }
            QLabel#startupStatus {
                color: #dbeafe;
                font-size: 13px;
            }
            QLabel#startupPercent {
                color: #bfdbfe;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#startupDetail {
                color: #64748b;
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center() - self.rect().center())

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
        if detail:
            self.detail_label.setText(detail)

    def mark_failed(self, message: str) -> None:
        self.status_label.setText("初始化失败")
        self.detail_label.setText(message)
        self.detail_label.setStyleSheet("color: #fca5a5;")
        self.exit_button.show()

