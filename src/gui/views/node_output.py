"""Compact node-local export form. No runtime data or action parameters here."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLineEdit, QVBoxLayout, QWidget

from ...domain.outputs import NodeOutput


class NodeOutputEditor(QWidget):
    content_changed = Signal()

    def __init__(self, output: NodeOutput | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(8)
        self.enabled = QCheckBox("将结果作为流程输出", self)
        self.enabled.setToolTip("仅影响当前流程节点；客户端可在任务完成响应或运行查询中获取。")
        layout.addWidget(self.enabled)
        self.details = QWidget(self)
        form = QFormLayout(self.details)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.name = QLineEdit(output.name if output else "vision_result")
        self.name.setPlaceholderText("例如 tube_liquid_level")
        form.addRow("输出名称", self.name)
        self.field = QComboBox()
        self.field.setEditable(True)
        self.field.addItem("全部结果", "")
        self.field.addItem("处理帧数", "/frames_processed")
        self.field.addItem("推理次数", "/inference_count")
        self.field.addItem("处理耗时（秒）", "/duration_seconds")
        self.field.setToolTip(
            "可选择现有字段，或输入 JSON Pointer，例如 /height；不会创建检测数据。"
        )
        if output and output.field:
            index = self.field.findData(output.field)
            if index >= 0:
                self.field.setCurrentIndex(index)
            else:
                self.field.setEditText(output.field)
        form.addRow("输出内容", self.field)
        self.mode = QComboBox()
        for label, value in (
            ("单次结果", "single"),
            ("收集全部（循环）", "all"),
            ("仅最后一次", "last"),
        ):
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(output.mode if output else "single"))
        form.addRow("结果收集", self.mode)
        self.required = QCheckBox("必需；未产生该字段则执行失败")
        self.required.setChecked(output.required if output else True)
        form.addRow(self.required)
        layout.addWidget(self.details)
        self.enabled.setChecked(output is not None)
        self.details.setVisible(output is not None)
        self.enabled.toggled.connect(self._toggle)

    def _toggle(self, enabled: bool) -> None:
        self.details.setVisible(enabled)
        self.content_changed.emit()

    def value(self) -> NodeOutput | None:
        if not self.enabled.isChecked():
            return None
        text = self.field.currentText().strip()
        index = self.field.findText(text)
        field = str(self.field.itemData(index)) if index >= 0 else text
        return NodeOutput(
            self.name.text().strip(), field, str(self.mode.currentData()), self.required.isChecked()
        )
