from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...domain.action_schema import (
    ActionFieldSchema,
    ActionTypeSchema,
    get_action_schema,
    validate_action_parameters,
)
from ...domain.models import ActionDefinition, ActionType
from ..theme import set_theme_role


class ActionPreviewDialog(QDialog):
    """Show an AI-expanded action sequence before explicit confirmation."""

    confirmed = Signal(bool)

    def __init__(
        self,
        items: list,
        skill_info: dict,
        *,
        risk: dict | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._skill_info = skill_info
        self._risk = dict(risk or {})
        self._risk_checkbox: QCheckBox | None = None
        self._init_ui()

    def _init_ui(self) -> None:
        skill_name = self._skill_info.get("name", "未知技能")
        icon = self._skill_info.get("icon", "🤖")
        estimated_time = self._skill_info.get("estimated_time", 0)
        self.setWindowTitle(f"动作预览 - {icon} {skill_name} ({len(self._items)}步)")
        self.setMinimumSize(500, 420)

        layout = QVBoxLayout(self)
        header = QLabel(f"{icon} {skill_name}\n{self._skill_info.get('description', '')}")
        header.setWordWrap(True)
        header.setObjectName("aiStatusCard")
        layout.addWidget(header)

        self.step_list = QListWidget()
        for index, item in enumerate(self._items, start=1):
            definition = item.get("definition", {})
            action_name = definition.get("name", "未知")
            action_type = definition.get("type", "未知")
            parameters = definition.get("parameters", {})
            row = QListWidgetItem(f"Step {index}: {action_name}")
            row.setToolTip(
                f"类型：{action_type}\n参数："
                + ", ".join(f"{key}={value}" for key, value in parameters.items())
            )
            if index <= 3:
                row.setForeground(QColor("#16a34a"))
            self.step_list.addItem(row)
        layout.addWidget(self.step_list, stretch=1)
        layout.addWidget(QLabel(f"⏱ 预计执行时间：~{estimated_time:.0f} 秒"))

        if self._risk.get("requires_acknowledgement") is True:
            warning = QLabel(
                "高风险动作：将控制物理设备。\n风险项："
                + ", ".join(self._risk.get("reasons") or [])
            )
            warning.setWordWrap(True)
            set_theme_role(warning, "danger")
            layout.addWidget(warning)
            self._risk_checkbox = QCheckBox(
                "我已核对动作、参数和现场环境，并确认执行"
            )
            layout.addWidget(self._risk_checkbox)

        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("✅ 确认执行")
        set_theme_role(confirm, "success")
        confirm.clicked.connect(self.accept_and_emit)
        buttons.addWidget(cancel)
        buttons.addStretch()
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

    def accept_and_emit(self) -> None:
        acknowledged = bool(
            self._risk_checkbox is not None
            and self._risk_checkbox.isChecked()
        )
        if self._risk.get("requires_acknowledgement") and not acknowledged:
            QMessageBox.warning(self, "需要风险确认", "请勾选风险确认后再执行。")
            return
        self.confirmed.emit(acknowledged)
        self.accept()


FieldWidget = QLineEdit | QComboBox | QSpinBox | QDoubleSpinBox | QCheckBox


class SchemaActionForm(QWidget):
    """Render one action parameter form solely from the canonical schema."""

    def __init__(
        self,
        action_type: ActionType,
        parameters: dict[str, Any] | None = None,
        *,
        initial_variant: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._action_type = action_type
        self._type_schema: ActionTypeSchema = get_action_schema()[action_type.value]
        self._values = dict(parameters or {})
        self._field_widgets: dict[str, FieldWidget] = {}
        self._field_schemas: dict[str, ActionFieldSchema] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        description = QLabel(self._type_schema.get("description", ""))
        description.setWordWrap(True)
        layout.addWidget(description)

        self._variant_combo: QComboBox | None = None
        variants = self._type_schema.get("variants")
        if variants is not None:
            variant_key = self._type_schema["variant_key"]
            selected = self._values.get(variant_key) or initial_variant
            self._variant_combo = QComboBox()
            for variant_name, variant_schema in variants.items():
                self._variant_combo.addItem(
                    variant_schema["description"],
                    variant_name,
                )
            selected_index = self._variant_combo.findData(selected)
            self._variant_combo.setCurrentIndex(max(0, selected_index))
            variant_form = QFormLayout()
            variant_form.addRow(
                f"{variant_key}:",
                self._variant_combo,
            )
            layout.addLayout(variant_form)

        self._fields_widget = QWidget()
        self._fields_layout = QFormLayout(self._fields_widget)
        layout.addWidget(self._fields_widget)
        note = self._type_schema.get("note")
        if note:
            note_label = QLabel(note)
            note_label.setWordWrap(True)
            set_theme_role(note_label, "muted")
            layout.addWidget(note_label)

        self._render_fields()
        if self._variant_combo is not None:
            self._variant_combo.currentIndexChanged.connect(
                self._change_variant
            )

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(self._field_widgets)

    @property
    def variant_names(self) -> tuple[str, ...]:
        variants = self._type_schema.get("variants", {})
        return tuple(variants)

    def parameters(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if self._variant_combo is not None:
            values[self._type_schema["variant_key"]] = (
                self._variant_combo.currentData()
            )
        for field_name, widget in self._field_widgets.items():
            value = self._widget_value(widget, self._field_schemas[field_name])
            if value is not None:
                values[field_name] = value
        return values

    def _change_variant(self) -> None:
        self._values = self.parameters()
        self._render_fields()

    def _render_fields(self) -> None:
        while self._fields_layout.rowCount():
            self._fields_layout.removeRow(0)
        self._field_widgets.clear()
        self._field_schemas.clear()

        fields = self._selected_fields()
        variant_key = self._type_schema.get("variant_key")
        for field_name, field_schema in fields.items():
            if field_name == variant_key:
                continue
            widget = self._create_widget(field_schema, self._values.get(field_name))
            label = field_schema.get("label", field_name)
            unit = field_schema.get("unit", "")
            required = " *" if field_schema.get("required") else ""
            suffix = f" ({unit})" if unit else ""
            self._fields_layout.addRow(f"{label}{suffix}{required}:", widget)
            self._field_widgets[field_name] = widget
            self._field_schemas[field_name] = field_schema

    def _selected_fields(self) -> dict[str, ActionFieldSchema]:
        variants = self._type_schema.get("variants")
        if variants is None:
            return self._type_schema.get("fields", {})
        assert self._variant_combo is not None
        return variants[str(self._variant_combo.currentData())]["fields"]

    def _create_widget(
        self,
        schema: ActionFieldSchema,
        current: Any,
    ) -> FieldWidget:
        value = schema.get("default") if current is None else current
        field_type = schema["type"]
        if field_type == "select":
            widget = QComboBox()
            for option in schema.get("options", []):
                option_value = option.get("value") if isinstance(option, dict) else option
                option_label = option.get("label") if isinstance(option, dict) else str(option)
                widget.addItem(str(option_label), option_value)
            index = widget.findData(value)
            widget.setCurrentIndex(max(0, index))
        elif field_type == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(value))
        elif field_type == "number" and _uses_integer_widget(schema):
            widget = QSpinBox()
            widget.setRange(int(schema.get("min", -2_147_483_647)), int(schema.get("max", 2_147_483_647)))
            widget.setValue(int(value or 0))
        elif field_type == "number":
            widget = QDoubleSpinBox()
            widget.setDecimals(6)
            widget.setRange(float(schema.get("min", -1e12)), float(schema.get("max", 1e12)))
            widget.setValue(float(value or 0.0))
        else:
            widget = QLineEdit()
            if field_type in {"object", "pose"}:
                widget.setText("" if value is None else json.dumps(value, ensure_ascii=False))
                widget.setPlaceholderText(
                    "JSON 对象，例如：{}"
                    if field_type == "object"
                    else "JSON 数组，例如：[x, y, z, rx, ry, rz]"
                )
            else:
                widget.setText("" if value is None else str(value))
                widget.setPlaceholderText(schema.get("placeholder", ""))
        widget.setEnabled(not schema.get("readonly", False))
        return widget

    @staticmethod
    def _widget_value(widget: FieldWidget, schema: ActionFieldSchema) -> Any:
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        text = widget.text().strip()
        if schema["type"] == "object":
            if not text:
                return None
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{schema.get('label', '对象字段')} 必须是合法 JSON 对象") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{schema.get('label', '对象字段')} 必须是 JSON 对象")
            return value
        if schema["type"] == "pose":
            if not text:
                return []
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("点位必须是合法 JSON 数组") from exc
            if (
                not isinstance(value, list)
                or len(value) != 6
                or any(
                    not isinstance(item, (int, float)) or isinstance(item, bool)
                    for item in value
                )
            ):
                raise ValueError("点位必须包含 6 个数值")
            return [float(item) for item in value]
        if not text and not schema.get("required") and "default" not in schema:
            return None
        return text


class ActionConfigDialog(QDialog):
    """Create or edit an action using the canonical schema-driven form."""

    def __init__(
        self,
        action_type: ActionType,
        action_data: dict[str, Any] | None = None,
        parent=None,
        *,
        existing_names: set[str] | None = None,
        initial_variant: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.action_type = action_type
        self.action_data = dict(action_data or {})
        self._existing_names = set(existing_names or ())
        current_name = str(self.action_data.get("name", ""))
        self._existing_names.discard(current_name)
        self._definition: ActionDefinition | None = None

        schema = get_action_schema()[action_type.value]
        self.setWindowTitle(f"配置 {schema.get('label', action_type.value)} 动作")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(current_name)
        form.addRow("动作名称 *:", self.name_input)
        layout.addLayout(form)
        self.action_form = SchemaActionForm(
            action_type,
            self.action_data.get("parameters", {}),
            initial_variant=_normalize_initial_variant(initial_variant),
        )
        layout.addWidget(self.action_form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "警告", "动作名称不能为空")
            self.name_input.setFocus()
            return
        if name in self._existing_names:
            QMessageBox.warning(self, "警告", f"动作名称已存在: {name}")
            self.name_input.selectAll()
            return
        try:
            parameters = self.action_form.parameters()
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return
        validation = validate_action_parameters(self.action_type, parameters)
        if not validation.is_valid:
            QMessageBox.warning(self, "参数错误", validation.message)
            return
        self._definition = ActionDefinition(
            id=str(self.action_data.get("id") or uuid4()),
            name=name,
            type=self.action_type,
            parameters=validation.parameters,
        )
        self.accept()

    def get_action_definition(self) -> ActionDefinition:
        if self._definition is None:
            raise RuntimeError("action definition is only available after acceptance")
        return ActionDefinition.from_dict(self._definition.to_dict())


def _uses_integer_widget(schema: ActionFieldSchema) -> bool:
    default = schema.get("default")
    limits = [schema.get("min"), schema.get("max")]
    return isinstance(default, int) and not isinstance(default, bool) and all(
        value is None or float(value).is_integer()
        for value in limits
    )


def _normalize_initial_variant(value: str | None) -> str | None:
    return {
        "机械臂移动": "机械臂",
        "身体移动": "身体",
    }.get(value, value)
