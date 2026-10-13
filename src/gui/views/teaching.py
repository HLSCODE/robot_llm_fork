"""Teaching workspace: controls and measured state, with no 3-D dependencies."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
    QFrame, QLabel, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ...application.teaching import MAX_ANGLE_DEG, MAX_SPEED_PERCENT, MAX_TRANSLATION_MM
from ...devices.runtime.arm_models import ArmState
from ..theme import set_theme_role
from .teaching_arrow import TeachingArrowButton


class TeachingView(QWidget):
    enter_requested = Signal()
    stop_requested = Signal()
    step_requested = Signal(bool, int, float, int)
    save_requested = Signal()
    hidden = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("workbenchCard", True)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(24)
        controls = QWidget()
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(16)
        title = QLabel("机械臂示教")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        self.arm = QComboBox()
        self.arm.addItem("左臂", "left")
        self.arm.addItem("右臂", "right")
        self.enter_button = QPushButton("进入示教")
        self.enter_button.clicked.connect(self.enter_requested)
        set_theme_role(self.enter_button, "primary")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("示教机械臂", self.arm)
        layout.addLayout(form)
        layout.addWidget(self.enter_button)
        self.motion_controls = QWidget()
        motion = QVBoxLayout(self.motion_controls)
        motion.setContentsMargins(0, 0, 0, 0)
        motion.setSpacing(12)
        self.mode = QComboBox()
        self.mode.addItem("笛卡尔单步移动", False)
        self.mode.addItem("关节单步移动", True)
        self.mode.currentIndexChanged.connect(self._update_axes)
        motion.addWidget(self.mode)
        self.frame_label = QLabel("参考坐标系：设备位姿参考系")
        self.frame_label.setWordWrap(True)
        motion.addWidget(self.frame_label)
        self.distance = QDoubleSpinBox()
        self.distance.setRange(0.1, MAX_TRANSLATION_MM)
        self.distance.setValue(1)
        self.distance.setSuffix(" mm")
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0.1, MAX_ANGLE_DEG)
        self.angle.setValue(1)
        self.angle.setSuffix(" °")
        self.speed = QSpinBox()
        self.speed.setRange(1, MAX_SPEED_PERCENT)
        self.speed.setValue(5)
        self.speed.setSuffix(" %")
        parameters = QFormLayout()
        parameters.setContentsMargins(0, 0, 0, 0)
        parameters.setHorizontalSpacing(12)
        parameters.setVerticalSpacing(12)
        parameters.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        parameters.addRow("平移步长", self.distance)
        parameters.addRow("转动步长", self.angle)
        parameters.addRow("速度比例", self.speed)
        motion.addLayout(parameters)
        self.axes_grid = QGridLayout()
        self.axes_grid.setContentsMargins(0, 8, 0, 8)
        self.axes_grid.setHorizontalSpacing(12)
        self.axes_grid.setVerticalSpacing(8)
        self.axes_grid.setColumnStretch(0, 1)
        self.axes_grid.setColumnStretch(2, 1)
        self.axis_rows: list[tuple[QLabel, QPushButton, QPushButton]] = []
        self.joint_count = 0
        self.joints_supported = False
        self._ensure_rows(6)
        motion.addLayout(self.axes_grid)
        self.save_button = QPushButton("保存当前位置为动作")
        self.save_button.clicked.connect(self.save_requested)
        motion.addWidget(self.save_button)
        layout.addWidget(self.motion_controls)
        self.stop_button = QPushButton("停止 / 退出示教")
        self.stop_button.setMinimumHeight(40)
        set_theme_role(self.stop_button, "danger")
        self.stop_button.clicked.connect(self.stop_requested)
        # Keep stop outside the scrolling controls, always reachable.
        note = QLabel("点击一次移动一步，不连续运动。\n软件停止不能替代实体急停。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.controls_panel = QWidget()
        self.controls_panel.setMinimumWidth(240)
        self.controls_panel.setMaximumWidth(360)
        left = QVBoxLayout(self.controls_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        left.addWidget(scroll)
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 12, 16, 16)
        footer.addWidget(self.stop_button)
        left.addLayout(footer)
        root.addWidget(self.controls_panel, 2)
        self.display_panel = QWidget()
        right = QVBoxLayout(self.display_panel)
        right.setContentsMargins(16, 20, 16, 16)
        right.setSpacing(16)
        self.placeholder = QLabel("三维姿态展示\n暂未启用，后续版本接入")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        set_theme_role(self.placeholder, "muted")
        right.addWidget(self.placeholder, 1)
        self.status = QLabel("未进入示教；不会自动连接或移动机械臂")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        self.pose_label = QLabel("实时位姿\n—")
        self.joints_label = QLabel("实时关节角\n—")
        for label in (self.pose_label, self.joints_label):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            right.addWidget(label)
        root.addWidget(self.display_panel, 3)
        self.set_session(active=False, ready=False, busy=False)
        self._update_axes()
        self.distance.valueChanged.connect(self._update_axis_hints)
        self.angle.valueChanged.connect(self._update_axis_hints)

    def _ensure_rows(self, count: int) -> None:
        while len(self.axis_rows) < count:
            axis = len(self.axis_rows)
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumWidth(32)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            minus = TeachingArrowButton(positive=False)
            plus = TeachingArrowButton(positive=True)
            for button, sign in ((minus, -1), (plus, 1)):
                button.setAutoRepeat(False)
                button.clicked.connect(
                    lambda _checked=False, index=axis, direction=sign: self._step(index, direction)
                )
            self.axes_grid.addWidget(minus, axis, 0)
            self.axes_grid.addWidget(label, axis, 1)
            self.axes_grid.addWidget(plus, axis, 2)
            self.axis_rows.append((label, minus, plus))

    def _step(self, axis: int, direction: int) -> None:
        if self._busy:
            return
        joint = bool(self.mode.currentData())
        amount = self.angle.value() if joint or axis >= 3 else self.distance.value()
        self.step_requested.emit(joint, axis, direction * amount, self.speed.value())

    def _update_axes(self) -> None:
        joint = bool(self.mode.currentData())
        count = self.joint_count if joint else 6
        self._ensure_rows(count)
        names = [f"J{i + 1}" for i in range(count)] if joint else ["X", "Y", "Z", "Rx", "Ry", "Rz"]
        for index, row in enumerate(self.axis_rows):
            for widget in row:
                widget.setVisible(index < count)
            if index < count:
                row[0].setText(names[index])
                for button, sign in zip(row[1:], ("−", "+"), strict=True):
                    button.setAccessibleName(f"{names[index]} {sign}")
                    button.setEnabled(not joint or self.joints_supported)
        self.distance.setEnabled(not joint)
        # Persisted joint actions currently have a seven-joint schema.
        self.save_button.setEnabled(
            not joint or (self.joints_supported and self.joint_count == 7)
        )
        self._update_axis_hints()

    def _update_axis_hints(self) -> None:
        joint = bool(self.mode.currentData())
        count = self.joint_count if joint else 6
        for axis, (label, minus, plus) in enumerate(self.axis_rows[:count]):
            angular = joint or axis >= 3
            amount = self.angle.value() if angular else self.distance.value()
            unit = "°" if angular else " mm"
            for button, direction in ((minus, "减小"), (plus, "增大")):
                # Fixed-reference rotations are not simple Euler-component increments.
                operation = direction
                if not joint and angular:
                    operation = "负向旋转" if direction == "减小" else "正向旋转"
                hint = f"{label.text()} {operation} {amount:g}{unit}（单步）"
                button.setAccessibleName(hint)
                button.setToolTip(hint)

    def render_state(self, state: ArmState, *, joints_supported: bool) -> None:
        joint_count = len(state.joints.positions_deg) if state.joints else 0
        if (joint_count, joints_supported) != (self.joint_count, self.joints_supported):
            self.joints_supported = joints_supported
            self.joint_count = joint_count
            self.mode.setItemText(1, "关节单步移动" if joints_supported else "关节移动（设备暂不支持）")
            self._update_axes()
        values = state.pose.to_list()
        self.pose_label.setText(
            "实时位姿\n" + "\n".join(
                f"{name}   {value * 1000:.2f} mm" for name, value in zip(("X", "Y", "Z"), values[:3], strict=True)
            ) + "\n" + "\n".join(
                f"{name}   {math.degrees(value):.2f}°" for name, value in zip(("Rx", "Ry", "Rz"), values[3:], strict=True)
            )
        )
        self.joints_label.setText("实时关节角\n" + (
            "  ·  ".join(f"J{i + 1}\u00a0{value:.2f}°" for i, value in enumerate(state.joints.positions_deg))
            if state.joints else "设备未提供关节角"
        ))

    def set_session(self, *, active: bool, ready: bool, busy: bool) -> None:
        self._busy = busy
        self.arm.setEnabled(not active and not busy)
        self.enter_button.setEnabled(not active and not busy)
        # A brief command must not grey out the entire panel or steal focus.
        # Repeated commands are rejected here and by the controller/service.
        self.motion_controls.setEnabled(active and ready)
        self.stop_button.setEnabled(active)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self.hidden.emit()
        super().hideEvent(event)
