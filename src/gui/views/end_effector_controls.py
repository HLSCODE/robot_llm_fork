"""Manual commands select a configured tool, never an inferred installed tool."""

from collections.abc import Callable

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel, QDoubleSpinBox, QLayout, QSizePolicy
from ...application.end_effectors import ToolPresentation
from ..theme import set_theme_role


class EndEffectorControls(QWidget):
    action_requested = Signal(object)

    def __init__(
        self,
        choices: Callable[[str, str | None], list[tuple[str, str]]],
        arms: tuple[str, ...],
        parent: QWidget | None = None,
        *, describe_tool: Callable[[str], ToolPresentation | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._choices = choices
        self._describe_tool = describe_tool
        self._execution_active = False
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)
        layout.addWidget(QLabel("末端工具控制"))
        if not arms:
            message = QLabel("未配置末端工具，请设置 [end_effectors] 后重启。")
            message.setWordWrap(True)
            layout.addWidget(message)
        self.arm = QComboBox()
        for arm in arms:
            self.arm.addItem("左臂" if arm == "left" else "右臂", arm)
        self.tool = QComboBox()
        self.tool.setPlaceholderText("该机械臂没有可用末端工具")
        self.tool.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.tool.setMinimumContentsLength(10)
        self.tool.setMinimumWidth(0)
        self.tool.setToolTip("选择配置的工具实例；这不表示该工具已经安装")
        row = QHBoxLayout()
        row.addWidget(self.arm)
        row.addWidget(self.tool, 1)
        layout.addLayout(row)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        layout.addLayout(self._buttons)
        self._position_row = QWidget()
        position_layout = QHBoxLayout(self._position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(8)
        position_layout.addWidget(QLabel("闭合度"))
        self.position = QDoubleSpinBox()
        self.position.setRange(0, 100)
        self.position.setSuffix(" %")
        position_layout.addWidget(self.position, 1)
        position_button = QPushButton("定位")
        position_button.clicked.connect(lambda: self._emit("定位", 闭合度=self.position.value()))
        position_layout.addWidget(position_button)
        layout.addWidget(self._position_row)
        self._rotation_row = QWidget()
        rotation_layout = QVBoxLayout(self._rotation_row)
        rotation_layout.setContentsMargins(0, 0, 0, 0)
        rotation_layout.setSpacing(8)
        rotation_fields = QHBoxLayout()
        rotation_fields.setSpacing(8)
        self.rotation_mode = QComboBox()
        self.rotation_mode.addItem("相对旋转", "相对旋转")
        self.rotation_mode.addItem("绝对旋转", "绝对旋转")
        self.angle = QDoubleSpinBox()
        self.angle.setDecimals(1)
        self.angle.setSuffix(" °")
        self.angle.setValue(0)
        rotation_fields.addWidget(self.rotation_mode)
        rotation_fields.addWidget(self.angle, 1)
        rotation_layout.addLayout(rotation_fields)
        rotation_buttons = QHBoxLayout()
        rotation_buttons.setSpacing(8)
        rotate = QPushButton("执行旋转")
        rotate.clicked.connect(lambda: self._emit(self.rotation_mode.currentData(), 旋转角度=self.angle.value()))
        stop = QPushButton("停止旋转")
        set_theme_role(stop, "danger")
        self.stop_rotation = stop
        stop.setToolTip("减速停止旋转，不是硬件急停；运行中的手动旋转通过取消当前动作停止")
        stop.clicked.connect(lambda: self._emit("停止旋转"))
        rotation_buttons.addWidget(rotate)
        rotation_buttons.addWidget(stop)
        rotation_layout.addLayout(rotation_buttons)
        layout.addWidget(self._rotation_row)
        self.arm.currentIndexChanged.connect(self._refresh_tools)
        self.tool.currentIndexChanged.connect(self._refresh_buttons)
        self._refresh_tools()
        self.setEnabled(bool(arms))

    def _refresh_tools(self) -> None:
        self.tool.clear()
        for kind in ("gripper", "pipette", "tool_changer"):
            for identity, label in self._choices(kind, self.arm.currentData()):
                self.tool.addItem(label, (kind, identity))
                description = self._describe_tool(identity) if self._describe_tool else None
                if description:
                    self.tool.setItemData(self.tool.count() - 1, description.details, Qt.ItemDataRole.ToolTipRole)
        if self.tool.count():
            self.tool.setCurrentIndex(0)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        while self._buttons.count():
            item = self._buttons.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        selected = self.tool.currentData()
        if selected is None:
            self._position_row.hide()
            self._rotation_row.hide()
            return
        kind, identity = selected
        description = self._describe_tool(identity) if self._describe_tool else None
        supported = description.operations if description else ("开", "关")
        self.tool.setToolTip(description.details if description else identity)
        self._position_row.setVisible(kind == "gripper" and "定位" in supported)
        self._rotation_row.setVisible(kind == "gripper" and "相对旋转" in supported)
        if description:
            self.angle.setRange(description.rotation_min, description.rotation_max)
        operations: tuple[tuple[str, str], ...] = {
            "gripper": (("张开", "开"), ("夹持", "关")),
            "pipette": (("初始化", "初始化"), ("退枪头", "退枪头")),
            "tool_changer": (("锁紧", "关"), ("解锁", "开")),
        }[kind]
        if kind == "gripper" and "初始化" in supported:
            operations = (*operations, ("初始化", "初始化"))
        executor = {"gripper": "夹爪", "pipette": "吸液枪", "tool_changer": "快换手"}[kind]
        for label, operation in operations:
            button = QPushButton(label)
            button.setMinimumHeight(32)
            parameters = {
                "执行器": executor,
                "操作": operation,
                "arm": self.arm.currentData(),
                "tool_id": identity,
            }
            button.clicked.connect(
                lambda _checked=False, command=parameters: self.action_requested.emit(command)
            )
            self._buttons.addWidget(button)
        self.set_execution_active(self._execution_active)
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
        self.updateGeometry()

    def set_execution_active(self, active: bool) -> None:
        self._execution_active = active
        self.arm.setEnabled(not active)
        self.tool.setEnabled(not active)
        self._position_row.setEnabled(not active)
        self.rotation_mode.setEnabled(not active)
        self.angle.setEnabled(not active)
        for index in range(self._buttons.count()):
            item = self._buttons.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setEnabled(not active)
        for button in self._rotation_row.findChildren(QPushButton):
            button.setEnabled(not active or button is self.stop_rotation)

    def _emit(self, operation: str, **parameters: float) -> None:
        selected = self.tool.currentData()
        if selected is None:
            return
        kind, identity = selected
        self.action_requested.emit({
            "执行器": {"gripper": "夹爪", "pipette": "吸液枪", "tool_changer": "快换手"}[kind],
            "操作": operation, "arm": self.arm.currentData(), "tool_id": identity, **parameters,
        })
