from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, QSize, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
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
    QSizePolicy,
    QStackedWidget,
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
from ..icons import IconName
from ..theme import set_theme_role
from ..toolbars import IconToolButton


class ActionPreviewDialog(QDialog):
    """Show an AI-expanded action sequence before explicit confirmation."""

    confirmed = Signal(bool)

    def __init__(
        self,
        items: list[dict[str, Any]],
        command_info: dict[str, Any],
        *,
        risk: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._command_info = command_info
        self._risk = dict(risk or {})
        self._risk_checkbox: QCheckBox | None = None
        self._init_ui()

    def _init_ui(self) -> None:
        command_name = self._command_info.get("name", "未命名命令")
        command_kind = self._command_info.get("kind", "command")
        estimated_time = float(self._command_info.get("estimated_time", 0) or 0)
        self.setWindowTitle(f"动作预览 - {command_name} ({len(self._items)}步)")
        self.setMinimumSize(500, 420)

        layout = QVBoxLayout(self)
        description = self._command_info.get("description", "")
        header = QLabel(f"{command_name}\n类型：{command_kind}\n{description}")
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


LocalizationReader = Callable[..., dict[str, Any] | None]
StationChoicesReader = Callable[[str | None], list[tuple[str, str]]]
PoseReader = Callable[[str], Sequence[float] | None]


class PoseReadWorker(QObject):
    """Read one arm pose away from the GUI thread."""

    succeeded = Signal(str, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, reader: PoseReader, arm: str) -> None:
        super().__init__()
        self._reader = reader
        self._arm = arm

    @Slot()
    def run(self) -> None:
        try:
            pose = self._reader(self._arm)
            if pose is None:
                raise RuntimeError(
                    f"{self._arm}臂当前位姿不可用，请确认设备已连接并完成初始化"
                )
            values = _validated_pose_values(pose)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(self._arm, values)
        finally:
            self.finished.emit()


class PoseEditor(QWidget):
    """Edit a pose or explicitly capture the selected arm's live pose."""

    def __init__(
        self,
        current: object = None,
        *,
        arm: str = "左",
        pose_reader: PoseReader | None = None,
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("poseEditor")
        self._arm = arm
        self._pose_reader = pose_reader
        self._read_thread: QThread | None = None
        self._read_worker: PoseReadWorker | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.input = QLineEdit(self)
        self.input.setObjectName("poseInput")
        self.input.setPlaceholderText(placeholder)
        if current is not None:
            self.input.setText(json.dumps(current, ensure_ascii=False))
        layout.addWidget(self.input, 1)

        self.read_button = IconToolButton(
            IconName.POSES,
            "读取当前机械臂位姿",
            callback=self.read_current_pose,
            parent=self,
            object_name="paneToolButton",
        )
        self.read_button.setProperty("featureName", "poseReadButton")
        self.read_button.setEnabled(pose_reader is not None)
        layout.addWidget(self.read_button)
        self._update_button_description()

    def set_arm(self, arm: str) -> None:
        self._arm = arm
        self._update_button_description()

    def text(self) -> str:
        return self.input.text()

    def read_current_pose(self) -> None:
        if self._pose_reader is None or self._read_thread is not None:
            return
        arm = self._arm
        if self.input.text().strip():
            answer = QMessageBox.question(
                self,
                "替换点位",
                f"是否使用{arm}臂当前位姿替换已有点位？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        thread = QThread(QApplication.instance())
        thread.setObjectName(f"PoseReadThread-{arm}")
        worker = PoseReadWorker(self._pose_reader, arm)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._apply_pose)
        worker.failed.connect(self._show_read_error)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._finish_read)
        thread.finished.connect(thread.deleteLater)
        self._read_thread = thread
        self._read_worker = worker
        self.read_button.setEnabled(False)
        self.read_button.setToolTip(f"正在读取{arm}臂当前位姿…")
        self.read_button.setAccessibleName(self.read_button.toolTip())
        thread.start()

    @Slot(str, object)
    def _apply_pose(self, arm: str, pose: object) -> None:
        if arm != self._arm or not isinstance(pose, list):
            return
        self.input.setText(
            "[" + ", ".join(f"{float(value):.6f}" for value in pose) + "]"
        )

    @Slot(str)
    def _show_read_error(self, message: str) -> None:
        QMessageBox.warning(self, "读取机械臂位姿", message)

    @Slot()
    def _finish_read(self) -> None:
        self._read_thread = None
        self._read_worker = None
        self.read_button.setEnabled(self._pose_reader is not None)
        self._update_button_description()

    def _update_button_description(self) -> None:
        description = f"读取{self._arm}臂当前位姿"
        self.read_button.setToolTip(description)
        self.read_button.setAccessibleName(description)


def _validated_pose_values(pose: Sequence[float]) -> list[float]:
    if isinstance(pose, (str, bytes)):
        raise ValueError("机械臂返回的当前位姿不是数值数组")
    if len(pose) != 6:
        raise ValueError("机械臂返回的当前位姿不是 6 维数组")
    if any(isinstance(value, bool) for value in pose):
        raise ValueError("机械臂返回的当前位姿包含无效数值")
    values = [float(value) for value in pose]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("机械臂返回的当前位姿包含无效数值")
    return values


class ContentSizedStackedWidget(QStackedWidget):
    """Report the visible page's height instead of the tallest page's height."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.currentChanged.connect(self.refresh_current_size)

    def sizeHint(self) -> QSize:  # noqa: N802
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def refresh_current_size(self) -> None:
        current = self.currentWidget()
        if current is not None:
            current.adjustSize()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()


class CompensationEditor(QWidget):
    """Edit the canonical move compensation object without exposing JSON."""

    def __init__(
        self,
        current: object = None,
        *,
        arm: str = "左",
        localization_reader: LocalizationReader | None = None,
        station_choices_reader: StationChoicesReader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("compensationEditor")
        self._localization_reader = localization_reader
        self._station_choices_reader = station_choices_reader
        self._arm = arm
        config = dict(current) if isinstance(current, dict) else {"mode": "none"}
        self._localization_reference = self._read_teach_offset(config)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.mode_combo = QComboBox(self)
        self.mode_combo.setObjectName("compensationModeCombo")
        for label, mode in (
            ("不补偿", "none"),
            ("UDP 定位补偿", "udp"),
            ("视觉重定位补偿", "vision"),
        ):
            self.mode_combo.addItem(label, mode)
        selected_mode = str(config.get("mode") or "none")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(selected_mode)))
        layout.addWidget(self.mode_combo)

        self.pages = ContentSizedStackedWidget(self)
        self.pages.setObjectName("compensationPages")
        self.pages.addWidget(self._build_none_page())
        self.pages.addWidget(self._build_udp_page())
        self.pages.addWidget(self._build_vision_page(config))
        layout.addWidget(self.pages)
        self.mode_combo.currentIndexChanged.connect(self._sync_mode_page)
        self._sync_mode_page()

    def value(self) -> dict[str, Any]:
        mode = str(self.mode_combo.currentData())
        if mode == "none":
            return {"mode": "none"}
        if mode == "udp":
            if self._localization_reference is None:
                raise ValueError("UDP 定位补偿需要先读取当前定位基准")
            return {
                "mode": "udp",
                "udp": {
                    "teach_offset": dict(self._localization_reference),
                    "udp_linear_unit": "cm",
                    "udp_angle_unit": "deg",
                    "pose_linear_unit": "m",
                    "pose_angle_unit": "rad",
                },
            }
        station_id = self._selected_station_id()
        if not station_id:
            raise ValueError("视觉重定位补偿需要选择视觉工位")
        return {
            "mode": "vision",
            "vision": {
                "station_id": station_id,
                "arm": self._normalized_arm(),
            },
        }

    def set_arm(self, arm: str) -> None:
        arm_changed = self._normalized_arm() != self._normalize_arm_value(arm)
        self._arm = arm
        if arm_changed:
            self._station_id = ""
            if hasattr(self, "station_combo"):
                self.station_combo.setEditText("")
        self._refresh_station_choices()

    def _build_none_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("compensationNonePage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("目标点位将直接发送给机械臂", page)
        set_theme_role(label, "muted")
        layout.addWidget(label)
        layout.addStretch(1)
        return page

    def _build_udp_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("compensationUdpPage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel("UDP 基准", page)
        title.setObjectName("compensationSectionLabel")
        self.localization_status = QLabel(page)
        self.localization_status.setObjectName("localizationReferenceStatus")
        set_theme_role(self.localization_status, "muted")
        self._render_localization_reference()
        capture_button = QPushButton("读取当前定位", page)
        capture_button.setObjectName("captureLocalizationButton")
        capture_button.clicked.connect(self._capture_localization_reference)
        layout.addWidget(title)
        layout.addWidget(self.localization_status, stretch=1)
        layout.addWidget(capture_button)
        return page

    def _build_vision_page(self, config: dict[str, Any]) -> QWidget:
        page = QWidget(self)
        page.setObjectName("compensationVisionPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel("视觉工位", page)
        title.setObjectName("compensationSectionLabel")
        self.station_combo = QComboBox(page)
        self.station_combo.setObjectName("visionStationCombo")
        self.station_combo.setEditable(True)
        self.station_status = QLabel(page)
        self.station_status.setObjectName("visionStationStatus")
        self.station_status.setWordWrap(True)
        set_theme_role(self.station_status, "muted")
        vision = config.get("vision")
        self._station_id = str(
            vision.get("station_id") if isinstance(vision, dict) else ""
        ).strip()
        self._refresh_station_choices()
        layout.addWidget(title)
        layout.addWidget(self.station_combo)
        layout.addWidget(self.station_status)
        hint = QLabel("请先执行同一工位和机械臂的视觉重定位动作", page)
        hint.setWordWrap(True)
        set_theme_role(hint, "muted")
        layout.addWidget(hint)
        return page

    def _sync_mode_page(self) -> None:
        mode = str(self.mode_combo.currentData())
        self.pages.setCurrentIndex({"none": 0, "udp": 1, "vision": 2}[mode])
        self.pages.refresh_current_size()
        self.updateGeometry()
        window = self.window()
        if isinstance(window, QDialog):
            QTimer.singleShot(0, window.adjustSize)

    def _capture_localization_reference(self) -> None:
        if self._localization_reader is None:
            QMessageBox.warning(self, "定位补偿", "定位服务未注入")
            return
        try:
            position = self._localization_reader(
                max_age=2.0,
                wait_timeout=0.0,
            )
        except Exception as exc:
            QMessageBox.warning(self, "定位补偿", f"读取 UDP 定位失败：\n{exc}")
            return
        if position is None:
            QMessageBox.warning(
                self,
                "定位补偿",
                "未收到当前有效定位数据，请确认 UDP Tag 已检测到",
            )
            return
        try:
            reference = {
                "id": position.get("id", -99),
                "x": float(position["x"]),
                "y": float(position["y"]),
                "angle": float(position["angle"]),
                "timestamp": float(position.get("timestamp", 0.0)),
            }
        except (KeyError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "定位补偿", f"UDP 定位数据格式无效：\n{exc}")
            return
        self._localization_reference = reference
        self._render_localization_reference()

    def _render_localization_reference(self) -> None:
        reference = self._localization_reference
        if reference is None:
            self.localization_status.setText("尚未读取基准")
            return
        self.localization_status.setText(
            f"ID={reference.get('id', -99)}  "
            f"X={float(reference['x']):.3f} cm  "
            f"Y={float(reference['y']):.3f} cm  "
            f"角度={float(reference['angle']):.3f}°"
        )

    def _refresh_station_choices(self) -> None:
        if not hasattr(self, "station_combo"):
            return
        current = self._selected_station_id() or self._station_id
        self.station_combo.blockSignals(True)
        self.station_combo.clear()
        try:
            choices = (
                self._station_choices_reader(self._normalized_arm())
                if self._station_choices_reader is not None
                else []
            )
        except Exception as exc:
            choices = []
            self.station_status.setText(f"视觉工位加载失败：{exc}")
            set_theme_role(self.station_status, "danger")
        else:
            self.station_status.setText(
                "" if choices else "当前机械臂尚未配置视觉工位"
            )
            set_theme_role(self.station_status, "muted")
        for station_id, label in choices:
            self.station_combo.addItem(f"{label} · {station_id}", station_id)
        if current:
            index = self.station_combo.findData(current)
            if index >= 0:
                self.station_combo.setCurrentIndex(index)
            else:
                self.station_combo.setEditText(current)
        elif self.station_combo.count() == 0:
            self.station_combo.setEditText("")
        self.station_combo.blockSignals(False)

    def _selected_station_id(self) -> str:
        if not hasattr(self, "station_combo"):
            return ""
        data = self.station_combo.currentData()
        if data:
            return str(data).strip()
        return self.station_combo.currentText().strip()

    def _normalized_arm(self) -> str:
        return self._normalize_arm_value(self._arm)

    @staticmethod
    def _normalize_arm_value(arm: str) -> str:
        return "right" if arm in {"右", "右臂", "right"} else "left"

    @staticmethod
    def _read_teach_offset(config: dict[str, Any]) -> dict[str, Any] | None:
        udp = config.get("udp")
        if not isinstance(udp, dict):
            return None
        reference = udp.get("teach_offset")
        return dict(reference) if isinstance(reference, dict) else None


FieldWidget = (
    QLineEdit
    | QComboBox
    | QSpinBox
    | QDoubleSpinBox
    | QCheckBox
    | PoseEditor
    | CompensationEditor
)


class SchemaActionForm(QWidget):
    """Render one action parameter form solely from the canonical schema."""

    def __init__(
        self,
        action_type: ActionType,
        parameters: dict[str, Any] | None = None,
        *,
        initial_variant: str | None = None,
        pose_reader: PoseReader | None = None,
        localization_reader: LocalizationReader | None = None,
        station_choices_reader: StationChoicesReader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._action_type = action_type
        self._type_schema: ActionTypeSchema = get_action_schema()[action_type.value]
        self._values = dict(parameters or {})
        self._field_widgets: dict[str, FieldWidget] = {}
        self._field_schemas: dict[str, ActionFieldSchema] = {}
        self._pose_reader = pose_reader
        self._localization_reader = localization_reader
        self._station_choices_reader = station_choices_reader

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
        arm_widget = self._field_widgets.get("臂")
        pose_widget = self._field_widgets.get("点位")
        compensation_widget = self._field_widgets.get("补偿")
        if isinstance(arm_widget, QComboBox) and isinstance(
            pose_widget,
            PoseEditor,
        ):
            arm_widget.currentTextChanged.connect(pose_widget.set_arm)
            pose_widget.set_arm(arm_widget.currentText())
        if isinstance(arm_widget, QComboBox) and isinstance(
            compensation_widget,
            CompensationEditor,
        ):
            arm_widget.currentTextChanged.connect(compensation_widget.set_arm)
            compensation_widget.set_arm(arm_widget.currentText())

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
        elif field_type == "compensation":
            arm_widget = self._field_widgets.get("臂")
            arm = arm_widget.currentText() if isinstance(arm_widget, QComboBox) else "左"
            widget = CompensationEditor(
                value,
                arm=arm,
                localization_reader=self._localization_reader,
                station_choices_reader=self._station_choices_reader,
            )
        elif field_type == "pose":
            arm_widget = self._field_widgets.get("臂")
            arm = arm_widget.currentText() if isinstance(arm_widget, QComboBox) else "左"
            widget = PoseEditor(
                value,
                arm=arm,
                pose_reader=self._pose_reader,
                placeholder=schema.get("placeholder", ""),
            )
        else:
            widget = QLineEdit()
            if field_type == "object":
                widget.setText("" if value is None else json.dumps(value, ensure_ascii=False))
                widget.setPlaceholderText("JSON 对象，例如：{}")
            else:
                widget.setText("" if value is None else str(value))
                widget.setPlaceholderText(schema.get("placeholder", ""))
        widget.setEnabled(not schema.get("readonly", False))
        return widget

    @staticmethod
    def _widget_value(widget: FieldWidget, schema: ActionFieldSchema) -> Any:
        if isinstance(widget, CompensationEditor):
            return widget.value()
        if isinstance(widget, PoseEditor):
            text = widget.text().strip()
            if not text:
                return []
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("点位必须是合法 JSON 数组") from exc
            try:
                return _validated_pose_values(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("点位必须包含 6 个有效数值") from exc
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
        if not text and not schema.get("required") and "default" not in schema:
            return None
        return text


class ActionConfigDialog(QDialog):
    """Create or edit an action using the canonical schema-driven form."""

    def __init__(
        self,
        action_type: ActionType,
        action_data: dict[str, Any] | None = None,
        parent: QWidget | None = None,
        *,
        existing_names: set[str] | None = None,
        initial_variant: str | None = None,
        pose_reader: PoseReader | None = None,
        localization_reader: LocalizationReader | None = None,
        station_choices_reader: StationChoicesReader | None = None,
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
            pose_reader=pose_reader,
            localization_reader=localization_reader,
            station_choices_reader=station_choices_reader,
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
    if value is None:
        return None
    return {
        "机械臂移动": "机械臂",
        "身体移动": "身体",
    }.get(value, value)
