from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..view_models.models import DeviceViewState
from ...domain.action_schema import get_action_fields
from ...domain.models import ActionType
from ..theme import set_theme_role


_DEVICE_STATUS_TITLES = {
    "robot": "机械臂",
    "body": "身体轴",
    "relay": "继电器",
}


class DeviceHealthView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._create_status_panel())
        self._end_effectors = QLabel()
        self._end_effectors.setTextFormat(Qt.TextFormat.PlainText)
        self._end_effectors.setWordWrap(True)
        self._end_effectors.setVisible(False)
        layout.addWidget(self._end_effectors)
        layout.addStretch(1)

    def render_end_effectors(self, mounts: list[dict[str, object]], states: list[dict[str, object]]) -> None:
        import datetime
        lines = []
        for mount in mounts:
            side = "左臂" if mount["arm"] == "left" else "右臂"
            if mount["mode"] == "none":
                lines.append(f"{side}：未安装末端工具")
                continue
            if mount["mode"] == "fixed":
                lines.append(f"{side}固定末端配置：{mount.get('tool_label', mount['tool'])}")
                if mount.get("unavailable"):
                    lines.append(str(mount["unavailable"]))
                continue
            state = next((s for s in states if s.get("arm") == mount["arm"]), {})
            locked = {"locked": "锁紧", "unlocked": "解锁"}.get(str(state.get("locked")), "未知")
            power = {"on": "开", "off": "关"}.get(str(state.get("power")), "未知")
            lines.append(f"{side}快换手：{locked} · 供电：{power}")
            sampled = state.get("sampled_at")
            if isinstance(sampled, (int, float)):
                lines.append(f"最近采样：{datetime.datetime.fromtimestamp(sampled):%H:%M:%S}")
            if state.get("error"):
                lines.append(f"通信异常：{state['error']}")
            tools = mount.get("tool_labels", mount.get("tools", ()))
            if isinstance(tools, (list, tuple)):
                lines.append("可选工具配置：" + "、".join(str(t) for t in tools))
        self._end_effectors.setVisible(bool(lines))
        self._end_effectors.setText("末端设备\n" + "\n".join(lines))

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
        self._status_rows: dict[str, QWidget] = {}
        for index, (key, status_title) in enumerate(_DEVICE_STATUS_TITLES.items()):
            widget, indicator, text = self._create_status_item(status_title, key)
            self._statuses[key] = (indicator, text)
            self._status_rows[key] = widget
            status_grid.addWidget(widget, index // 2, index % 2)
        layout.addLayout(status_grid)
        camera_title = QLabel("相机")
        camera_title_font = camera_title.font()
        camera_title_font.setBold(True)
        camera_title.setFont(camera_title_font)
        layout.addWidget(camera_title)
        self._camera_layout = QVBoxLayout()
        self._camera_layout.setContentsMargins(0, 0, 0, 0)
        self._camera_layout.setSpacing(4)
        layout.addLayout(self._camera_layout)
        self.render_cameras(())
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
            "relay": state.relay_ready,
        }
        for key, ready in readiness.items():
            indicator, label = self._statuses[key]
            color = "#22c55e" if ready else "#ef4444"
            indicator.setStyleSheet(f"background-color: {color}; border-radius: 8px;")
            status_text = "已连接" if ready else "未连接"
            label.setText(f"{_DEVICE_STATUS_TITLES[key]}: {status_text}")

    def render_cameras(self, cameras: tuple[dict[str, object], ...]) -> None:
        while self._camera_layout.count():
            item = self._camera_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not cameras:
            label = QLabel("尚未检测")
            label.setObjectName("cameraStatusEmpty")
            self._camera_layout.addWidget(label)
            return
        for camera in cameras:
            label_text = str(camera.get("label") or camera.get("name") or "未命名相机")
            healthy = camera.get("frame_received") is True
            required = camera.get("required") is True
            error = str(camera.get("error") or "未获得有效帧")
            if healthy:
                status_text = "正常"
                color = "#22c55e"
            elif error == "未连接" or "未找到" in error:
                status_text = "未连接"
                color = "#ef4444" if required else "#f59e0b"
            else:
                status_text = f"启动失败：{error}"
                color = "#ef4444" if required else "#f59e0b"
            row = QLabel(f"●  {label_text}：{status_text}")
            row.setWordWrap(True)
            row.setStyleSheet(f"color: {color};")
            row.setToolTip(error if not healthy else f"{label_text} 工作正常")
            self._camera_layout.addWidget(row)


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
    relay_requested = Signal(int, bool)
    body_move_requested = Signal(int)
    execution_stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        layout = QVBoxLayout(self._content)
        self._controls_layout = layout
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        selector = QHBoxLayout()
        selector.setSpacing(8)
        selector.addWidget(QLabel("控制设备"))
        self.device_selector = QComboBox()
        self.device_selector.setAccessibleName("选择控制设备")
        for label, identity in (("末端工具", "tools"), ("继电器", "relay"), ("身体升降", "body")):
            self.device_selector.addItem(label, identity)
        selector.addWidget(self.device_selector, 1)
        layout.addLayout(selector)
        self._tool_page = QWidget()
        self._tool_layout = QVBoxLayout(self._tool_page)
        self._tool_layout.setContentsMargins(0, 0, 0, 0)
        self._tool_placeholder = QLabel("未配置末端工具")
        self._tool_layout.addWidget(self._tool_placeholder)
        layout.addWidget(self._tool_page)
        group = QWidget()
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
        self._body_page = QWidget()
        body_layout = QVBoxLayout(self._body_page)
        body_layout.setContentsMargins(0, 8, 0, 0)
        body_layout.setSpacing(12)
        self.body_status = QLabel("身体控制器未就绪，请先在设备菜单中初始化")
        self.body_status.setWordWrap(True)
        body_layout.addWidget(self.body_status)
        fields, _ = get_action_fields(ActionType.MOVE, {"目标": "身体"})
        assert fields is not None
        position_schema = fields["位置"]
        self.body_position = QSpinBox()
        self.body_position.setRange(int(position_schema["min"]), int(position_schema["max"]))
        self.body_position.setValue(int(position_schema["default"]))
        self.body_position.setSuffix(" 脉冲")
        self.body_position.setAccessibleName("身体升降目标位置（脉冲）")
        body_form = QFormLayout()
        body_form.addRow("目标位置", self.body_position)
        body_layout.addLayout(body_form)
        self.body_move_button = self._button("移动到目标", lambda: self.body_move_requested.emit(self.body_position.value()))
        body_layout.addWidget(self.body_move_button)
        hint = QLabel("绝对位置控制，单位为驱动器脉冲。请先确认行程和周围空间；当前接口不提供实时位置读取。")
        hint.setWordWrap(True)
        set_theme_role(hint, "muted")
        body_layout.addWidget(hint)
        layout.addWidget(self._body_page)
        self._pages = {"tools": self._tool_page, "relay": group, "body": self._body_page}
        self._execution_active = False
        self._state = DeviceViewState(False, False, False)
        layout.addStretch(1)
        self.scroll_area.setWidget(self._content)
        outer.addWidget(self.scroll_area)
        self.stop_button = self._button("停止当前任务", self.execution_stop_requested.emit)
        set_theme_role(self.stop_button, "danger")
        self.stop_button.setToolTip("请求取消当前执行任务；身体升降将在取消检查点发送软件停止指令。不能替代实体急停。")
        outer.addWidget(self.stop_button)
        self.device_selector.currentIndexChanged.connect(self._select_device)
        self._select_device()
        self.render_state(self._state)
        self._content.installEventFilter(self)
        self.setObjectName("deviceControl")

    def sizeHint(self) -> QSize:  # noqa: N802
        margins = self.contentsMargins()
        return self._content.sizeHint() + QSize(
            16 + margins.left() + margins.right(), margins.top() + margins.bottom()
            + max(self.stop_button.sizeHint().height(), self.stop_button.minimumHeight())
            + self._outer_layout.spacing(),
        )

    def _select_device(self) -> None:
        selected = self.device_selector.currentData()
        for identity, page in self._pages.items():
            page.setVisible(identity == selected)
        self._controls_layout.invalidate()
        self.scroll_area.verticalScrollBar().setValue(0)
        self.updateGeometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._content and event.type() is QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)

    @staticmethod
    def _button(text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumHeight(28)
        button.clicked.connect(lambda: callback())
        return button

    def render_state(self, state: DeviceViewState) -> None:
        self._state = state
        for button in self._relay_buttons:
            button.setEnabled(state.relay_ready and not self._execution_active)
            button.setToolTip("" if state.relay_ready else "继电器未就绪，请先在设备菜单中初始化")
        self.body_position.setEnabled(state.body_ready and not self._execution_active)
        self.body_move_button.setEnabled(state.body_ready and not self._execution_active)
        self.body_status.setText("身体控制器已就绪" if state.body_ready else "身体控制器未就绪，请先在设备菜单中初始化")
        self.stop_button.setEnabled(self._execution_active)

    def set_execution_active(self, active: bool) -> None:
        self._execution_active = active
        self.render_state(self._state)

    def use_configured_end_effectors(self, controls: QWidget) -> None:
        self._tool_placeholder.hide()
        self._tool_layout.addWidget(controls)
