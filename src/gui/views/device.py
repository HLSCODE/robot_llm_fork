from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..view_models.models import DeviceViewState


_DEVICE_STATUS_TITLES = {
    "robot": "机械臂",
    "body": "身体轴",
    "pipette": "移液器",
    "relay": "继电器",
}


class DeviceHealthView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._create_status_panel())
        layout.addStretch(1)

    def _create_status_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        title = QLabel("🔌 设备状态")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        status_grid = QGridLayout()
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setHorizontalSpacing(24)
        status_grid.setVerticalSpacing(8)
        status_grid.setColumnStretch(0, 1)
        status_grid.setColumnStretch(1, 1)
        self._statuses: dict[str, tuple[QLabel, QLabel]] = {}
        for index, (key, status_title) in enumerate(_DEVICE_STATUS_TITLES.items()):
            widget, indicator, text = self._create_status_item(status_title, key)
            self._statuses[key] = (indicator, text)
            status_grid.addWidget(widget, index // 2, index % 2)
        layout.addLayout(status_grid)
        return panel

    @staticmethod
    def _create_status_item(title: str, key: str) -> tuple[QWidget, QLabel, QLabel]:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        indicator = QLabel()
        indicator.setFixedSize(16, 16)
        indicator.setObjectName(f"{key}_indicator")
        text = QLabel(f"{title}: 未连接")
        text.setObjectName(f"{key}_status_text")
        layout.addWidget(indicator)
        layout.addWidget(text)
        layout.addStretch()
        return widget, indicator, text

    def render_state(self, state: DeviceViewState) -> None:
        readiness = {
            "robot": state.robot_ready,
            "body": state.body_ready,
            "pipette": state.pipette_ready,
            "relay": state.relay_ready,
        }
        for key, ready in readiness.items():
            indicator, label = self._statuses[key]
            color = "#22c55e" if ready else "#ef4444"
            indicator.setStyleSheet(f"background-color: {color}; border-radius: 8px;")
            status_text = "已连接" if ready else "未连接"
            label.setText(f"{_DEVICE_STATUS_TITLES[key]}: {status_text}")


class DevicePoseView(QWidget):
    refresh_requested = Signal()
    copy_pose_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._create_pose_panel())
        layout.addStretch(1)

    def _create_pose_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("机械臂位姿")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        refresh = QPushButton("刷新")
        refresh.setObjectName("poseRefreshButton")
        refresh.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        refresh.clicked.connect(lambda: self.refresh_requested.emit())
        header.addWidget(title)
        header.addStretch()
        header.addWidget(refresh)
        layout.addLayout(header)
        self._pose_labels = {
            "robot1": self._add_pose_row(layout, "R1", "robot1"),
            "robot2": self._add_pose_row(layout, "R2", "robot2"),
        }
        self._localization_label = self._add_localization_row(layout)
        return panel

    def _add_pose_row(self, parent: QVBoxLayout, title: str, robot_name: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(f"{title}:")
        label_font = label.font()
        label_font.setBold(True)
        label.setFont(label_font)
        value = QLabel("--")
        value.setObjectName(f"{robot_name}PoseValue")
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        value.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy = QPushButton("复制")
        copy.setObjectName(f"{robot_name}PoseCopyButton")
        copy.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        copy.clicked.connect(
            lambda _checked=False, name=robot_name: self.copy_pose_requested.emit(name)
        )
        row.addWidget(label, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(value, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(copy, alignment=Qt.AlignmentFlag.AlignTop)
        parent.addLayout(row)
        return value

    @staticmethod
    def _add_localization_row(parent: QVBoxLayout) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel("底盘:")
        label_font = label.font()
        label_font.setBold(True)
        label.setFont(label_font)
        value = QLabel("--")
        value.setObjectName("localizationPoseValue")
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        value.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(label, alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(value, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
        parent.addLayout(row)
        return value

    def render_pose(self, robot_name: str, text: str) -> None:
        self._pose_labels[robot_name].setText(text)

    def render_localization(self, text: str) -> None:
        self._localization_label.setText(text)


class DeviceControlView(QFrame):
    gripper_requested = Signal(bool)
    relay_requested = Signal(int, bool)
    pipette_eject_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        title = QLabel("基础控制")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._gripper_buttons = [
            self._button("🔓 夹爪打开", lambda: self.gripper_requested.emit(True)),
            self._button("🔒 夹爪关闭", lambda: self.gripper_requested.emit(False)),
        ]
        self._pipette_button = self._button("💉 退枪头", self.pipette_eject_requested.emit)
        for button in (*self._gripper_buttons, self._pipette_button):
            row.addWidget(button)
        layout.addLayout(row)

        group = QGroupBox("继电器控制")
        relay_row = QHBoxLayout(group)
        relay_row.setContentsMargins(8, 6, 8, 6)
        relay_row.setSpacing(6)
        self._relay_buttons = []
        for channel, enabled in ((1, True), (1, False), (2, True), (2, False)):
            button = self._button(
                f"Y{channel} {'开' if enabled else '关'}",
                partial(self.relay_requested.emit, channel, enabled),
            )
            self._relay_buttons.append(button)
            relay_row.addWidget(button)
        layout.addWidget(group)
        self.setObjectName("deviceControl")

    @staticmethod
    def _button(text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumHeight(28)
        button.clicked.connect(lambda: callback())
        return button

    def render_state(self, state: DeviceViewState) -> None:
        for button in self._gripper_buttons:
            button.setEnabled(state.robot_ready)
        for button in self._relay_buttons:
            button.setEnabled(state.relay_ready)

    def set_pipette_action_enabled(self, enabled: bool) -> None:
        self._pipette_button.setEnabled(enabled)
