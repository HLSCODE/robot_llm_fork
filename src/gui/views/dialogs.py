from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
from ..app_dialogs import (
    AppDialog,
    ask_confirmation,
    create_dialog_button_box,
    show_warning,
)
from ..icons import IconName
from ..theme import set_theme_role
from ..toolbars import IconToolButton


class ActionPreviewDialog(AppDialog):
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

        layout = self.content_layout
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
        confirm = QPushButton("确认执行")
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
            show_warning(self, "需要风险确认", "请勾选风险确认后再执行。")
            return
        self.confirmed.emit(acknowledged)
        self.accept()


LocalizationReader = Callable[..., dict[str, Any] | None]
StationChoicesReader = Callable[[str | None], list[tuple[str, str]]]
PoseReader = Callable[[str], Sequence[float] | None]


class FormFieldLabel(QWidget):
    """Render a form label with a semantic required indicator."""

    def __init__(
        self,
        text: str,
        *,
        required: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(QLabel(text, self))
        if required:
            indicator = QLabel("*", self)
            indicator.setObjectName("requiredFieldIndicator")
            indicator.setAccessibleName("必填")
            set_theme_role(indicator, "danger")
            layout.addWidget(indicator)
        layout.addWidget(QLabel("：", self))


def _set_widget_validation_state(widget: QWidget, invalid: bool) -> None:
    widget.setProperty("validationState", "error" if invalid else "")
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


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
            should_replace = ask_confirmation(
                self,
                "替换点位",
                f"是否使用{arm}臂当前位姿替换已有点位？",
            )
            if not should_replace:
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
        show_warning(self, "读取机械臂位姿", message)

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
            _set_widget_validation_state(self.station_combo, True)
            raise ValueError("视觉重定位补偿需要选择视觉工位")
        _set_widget_validation_state(self.station_combo, False)
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
        self.station_combo.currentTextChanged.connect(
            lambda _text: _set_widget_validation_state(
                self.station_combo,
                False,
            )
        )
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
            show_warning(self, "定位补偿", "定位服务未注入")
            return
        try:
            position = self._localization_reader(
                max_age=2.0,
                wait_timeout=0.0,
            )
        except Exception as exc:
            show_warning(self, "定位补偿", f"读取 UDP 定位失败：\n{exc}")
            return
        if position is None:
            show_warning(
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
            show_warning(self, "定位补偿", f"UDP 定位数据格式无效：\n{exc}")
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

    content_size_changed = Signal()

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
        self._locked_variant = initial_variant
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        variants = self._type_schema.get("variants")
        if self._locked_variant is not None and (
            variants is None or self._locked_variant not in variants
        ):
            raise ValueError(f"unknown action variant: {self._locked_variant}")
        description_text = self._type_schema.get("description", "")
        if variants is not None and self._locked_variant is not None:
            description_text = variants[self._locked_variant]["description"]
        description = QLabel(description_text)
        description.setWordWrap(True)
        layout.addWidget(description)

        self._variant_combo: QComboBox | None = None
        if variants is not None and self._locked_variant is None:
            variant_key = self._type_schema["variant_key"]
            selected = self._values.get(variant_key)
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
                f"{self._type_schema.get('variant_label', variant_key)}:",
                self._variant_combo,
            )
            layout.addLayout(variant_form)

        self._fields_widget = QWidget()
        self._fields_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
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
        if self._locked_variant is not None:
            return (self._locked_variant,)
        variants = self._type_schema.get("variants", {})
        return tuple(variants)

    def parameters(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        variants = self._type_schema.get("variants")
        if variants is not None:
            values[self._type_schema["variant_key"]] = self._selected_variant_name()
        for field_name, widget in self._field_widgets.items():
            try:
                value = self._widget_value(widget, self._field_schemas[field_name])
            except ValueError:
                self._set_field_invalid(widget, True)
                self._validation_target(widget).setFocus()
                raise
            if value is not None:
                values[field_name] = value
        return values

    def mark_missing_required_fields(self) -> tuple[str, ...]:
        """Mark every empty schema-required field and return their names."""
        missing: list[str] = []
        for field_name, schema in self._field_schemas.items():
            if not schema.get("required"):
                continue
            widget = self._field_widgets[field_name]
            is_missing = not self._field_has_value(widget)
            self._set_field_invalid(widget, is_missing)
            if is_missing:
                missing.append(field_name)
        return tuple(missing)

    def mark_fields_invalid(self, field_names: Sequence[str]) -> None:
        invalid_names = set(field_names)
        for field_name, widget in self._field_widgets.items():
            self._set_field_invalid(widget, field_name in invalid_names)

    def focus_field(self, field_name: str) -> None:
        widget = self._field_widgets.get(field_name)
        if widget is not None:
            self._validation_target(widget).setFocus()

    def _change_variant(self) -> None:
        self._values.update(self.parameters())
        self._render_fields()
        self.adjustSize()
        self.updateGeometry()
        self.content_size_changed.emit()

    def _render_fields(self) -> None:
        while self._fields_layout.rowCount():
            self._fields_layout.removeRow(0)
        self._field_widgets.clear()
        self._field_schemas.clear()

        fields = self._selected_fields()
        variant_key = self._type_schema.get("variant_key")
        for field_name, field_schema in fields.items():
            if field_name == variant_key or field_schema.get("hidden", False):
                continue
            widget = self._create_widget(field_schema, self._values.get(field_name))
            label = field_schema.get("label", field_name)
            unit = field_schema.get("unit", "")
            suffix = f" ({unit})" if unit else ""
            self._fields_layout.addRow(
                FormFieldLabel(
                    f"{label}{suffix}",
                    required=bool(field_schema.get("required")),
                ),
                widget,
            )
            self._field_widgets[field_name] = widget
            self._field_schemas[field_name] = field_schema
            self._connect_validation_reset(widget)
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
        relocalization_arm = self._field_widgets.get("arm")
        station_widget = self._field_widgets.get("station_id")
        if isinstance(relocalization_arm, QComboBox) and isinstance(
            station_widget,
            QComboBox,
        ):
            relocalization_arm.currentIndexChanged.connect(
                lambda _index: self._populate_station_choices(
                    station_widget,
                    None,
                )
            )

    def _selected_fields(self) -> dict[str, ActionFieldSchema]:
        variants = self._type_schema.get("variants")
        if variants is None:
            return self._type_schema.get("fields", {})
        return variants[self._selected_variant_name()]["fields"]

    def _selected_variant_name(self) -> str:
        if self._locked_variant is not None:
            return self._locked_variant
        assert self._variant_combo is not None
        return str(self._variant_combo.currentData())

    def _create_widget(
        self,
        schema: ActionFieldSchema,
        current: Any,
    ) -> FieldWidget:
        value = schema.get("default") if current is None else current
        field_type = schema["type"]
        if field_type == "select":
            widget = QComboBox()
            if schema.get("options_source") == "vision_stations":
                self._populate_station_choices(widget, value)
            else:
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

    def _populate_station_choices(
        self,
        widget: QComboBox,
        selected_station_id: object,
    ) -> None:
        arm_widget = self._field_widgets.get("arm")
        arm = "left"
        if isinstance(arm_widget, QComboBox):
            arm_data = arm_widget.currentData()
            if isinstance(arm_data, str) and arm_data:
                arm = arm_data
        choices = (
            self._station_choices_reader(arm)
            if self._station_choices_reader is not None
            else []
        )
        selected = str(selected_station_id or "").strip()
        widget.blockSignals(True)
        try:
            widget.clear()
            widget.addItem("请选择示教工位", "")
            for station_id, label in choices:
                widget.addItem(label, station_id)
            selected_index = widget.findData(selected)
            if selected and selected_index < 0:
                widget.addItem(f"{selected}（当前配置）", selected)
                selected_index = widget.count() - 1
            widget.setCurrentIndex(max(0, selected_index))
        finally:
            widget.blockSignals(False)

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

    @staticmethod
    def _validation_target(widget: FieldWidget) -> QWidget:
        if isinstance(widget, PoseEditor):
            return widget.input
        return widget

    @classmethod
    def _set_field_invalid(cls, widget: FieldWidget, invalid: bool) -> None:
        target = cls._validation_target(widget)
        _set_widget_validation_state(target, invalid)

    @classmethod
    def _field_has_value(cls, widget: FieldWidget) -> bool:
        target = cls._validation_target(widget)
        if isinstance(target, QLineEdit):
            return bool(target.text().strip())
        if isinstance(target, QComboBox):
            return target.currentIndex() >= 0 and target.currentData() not in {None, ""}
        if isinstance(target, QCheckBox):
            return target.isChecked()
        return True

    def _connect_validation_reset(self, widget: FieldWidget) -> None:
        target = self._validation_target(widget)
        if isinstance(target, QLineEdit):
            target.textChanged.connect(
                lambda _text, field=widget: self._set_field_invalid(field, False)
            )
        elif isinstance(target, QComboBox):
            target.currentIndexChanged.connect(
                lambda _index, field=widget: self._set_field_invalid(field, False)
            )
        elif isinstance(target, (QSpinBox, QDoubleSpinBox)):
            target.valueChanged.connect(
                lambda _value, field=widget: self._set_field_invalid(field, False)
            )
        elif isinstance(target, QCheckBox):
            target.toggled.connect(
                lambda _checked, field=widget: self._set_field_invalid(field, False)
            )


class ActionConfigDialog(AppDialog):
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
        layout = self.content_layout
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = QFormLayout()
        self.name_input = QLineEdit(current_name)
        form.addRow(
            FormFieldLabel("动作名称", required=True),
            self.name_input,
        )
        self.name_input.textChanged.connect(
            lambda _text: self._set_name_invalid(False)
        )
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
        self.action_form.content_size_changed.connect(
            self._schedule_content_resize
        )
        buttons = create_dialog_button_box(self.content)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QTimer.singleShot(0, self._adjust_to_content)

    def _schedule_content_resize(self) -> None:
        QTimer.singleShot(0, self._adjust_to_content)

    def _adjust_to_content(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.action_form.adjustSize()
        self.adjustSize()

    def _validate_and_accept(self) -> None:
        name = self.name_input.text().strip()
        name_missing = not name
        self._set_name_invalid(name_missing)
        missing_fields = self.action_form.mark_missing_required_fields()
        if name_missing or missing_fields:
            missing_labels = (["动作名称"] if name_missing else []) + list(
                missing_fields
            )
            show_warning(
                self,
                "请填写必填项",
                "请填写：" + "、".join(missing_labels),
            )
            if name_missing:
                self.name_input.setFocus()
            else:
                self.action_form.focus_field(missing_fields[0])
            return
        if name in self._existing_names:
            self._set_name_invalid(True)
            show_warning(self, "警告", f"动作名称已存在: {name}")
            self.name_input.selectAll()
            return
        try:
            parameters = self.action_form.parameters()
        except ValueError as exc:
            show_warning(self, "参数错误", str(exc))
            return
        validation = validate_action_parameters(self.action_type, parameters)
        if not validation.is_valid:
            invalid_fields = tuple(issue.field for issue in validation.issues)
            self.action_form.mark_fields_invalid(invalid_fields)
            if invalid_fields:
                self.action_form.focus_field(invalid_fields[0])
            show_warning(self, "参数错误", validation.message)
            return
        self._definition = ActionDefinition(
            id=str(self.action_data.get("id") or uuid4()),
            name=name,
            type=self.action_type,
            parameters=validation.parameters,
        )
        self.accept()

    def _set_name_invalid(self, invalid: bool) -> None:
        _set_widget_validation_state(self.name_input, invalid)

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
        "机械臂相对移动": "机械臂相对",
        "身体移动": "身体",
    }.get(value, value)
