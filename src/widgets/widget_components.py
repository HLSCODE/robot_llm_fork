from PyQt6.QtWidgets import (QWidget, QListWidget, QListWidgetItem, QLabel,
                            QPushButton, QVBoxLayout, QHBoxLayout, QTextEdit,
                            QTabWidget)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QMimeData
from PyQt6.QtGui import QIcon, QColor, QDrag
import json
from ..core.models import ActionDefinition, SequenceItem, ActionType, SequenceItemStatus


class ActionListWidget(QListWidget):
    action_selected = pyqtSignal(ActionDefinition)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        # 竖屏适配：使用列表模式
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(24, 24))
        self.setSpacing(2)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)

    def startDrag(self, supportedActions):
        current_item = self.currentItem()
        if current_item:
            action = current_item.data(Qt.ItemDataRole.UserRole)
            if action:
                mime = QMimeData()
                mime.setData("application/x-action", json.dumps(action.to_dict()).encode('utf-8'))

                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.setPixmap(self.currentItem().icon().pixmap(50, 50))
                drag.exec(Qt.DropAction.CopyAction)

    def add_action(self, action: ActionDefinition):
        item = QListWidgetItem()
        item.setText(action.name)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        item.setSizeHint(QSize(100, 36))  # 列表模式下的行高

        icon = self._get_icon_for_type(action.type)
        item.setIcon(icon)

        item.setData(Qt.ItemDataRole.UserRole, action)
        self.addItem(item)

    def get_selected_action(self) -> ActionDefinition:
        current = self.currentItem()
        if current:
            return current.data(Qt.ItemDataRole.UserRole)
        return None

    def _get_icon_for_type(self, action_type: ActionType) -> QIcon:
        colors = {
            ActionType.MOVE: QColor(100, 149, 237),  # 机械臂移动 - 蓝色
            ActionType.BASE_MOVE: QColor(255, 99, 71),  # 底盘移动 - 红色
            ActionType.MANIPULATE: QColor(255, 140, 0),
            ActionType.WAIT: QColor(255, 140, 0),
            ActionType.INSPECT: QColor(60, 179, 113),
            ActionType.CHANGE_GUN: QColor(147, 112, 219),
            ActionType.VISION_CAPTURE: QColor(30, 144, 255),
        }
        color = colors.get(action_type, QColor(128, 128, 128))
        return self._create_colored_icon(color)

    def _create_colored_icon(self, color: QColor) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter
        # 列表模式下使用更小的图标
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 20, 20, 4, 4)
        painter.end()
        return QIcon(pixmap)


class SequenceListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setDragEnabled(False)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # 横向流动：图标模式，每项较大卡片
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setSpacing(12)
        self.setIconSize(QSize(120, 80))
        self.setStyleSheet("""
            QListWidget {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
            QListWidget::item {
                border: 2px solid #ddd;
                border-radius: 8px;
                padding: 2px;
                font-size: 11px;
                font-weight: bold;
            }
            QListWidget::item:selected {
                border: 2px solid #2196F3;
            }
        """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-action"):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-action"):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-action"):
            data = event.mimeData().data("application/x-action")
            action_dict = json.loads(data.data().decode('utf-8'))
            action = ActionDefinition.from_dict(action_dict)
            sequence_item = SequenceItem.from_definition(action)
            self.add_sequence_item(sequence_item)
            event.accept()
        else:
            super().dropEvent(event)

    def add_sequence_item(self, item: SequenceItem):
        list_item = QListWidgetItem()
        current_index = self.count()  # 添加前已有数量，即新项的序号
        self._update_item_display(list_item, item, current_index)
        list_item.setData(Qt.ItemDataRole.UserRole, item)
        self.addItem(list_item)

    def update_item_status(self, index: int, item: SequenceItem):
        if 0 <= index < self.count():
            list_item = self.item(index)
            self._update_item_display(list_item, item, index)
            list_item.setData(Qt.ItemDataRole.UserRole, item)

    def _update_item_display(self, list_item: QListWidgetItem, item: SequenceItem, index: int):
        status_text = self._get_status_text(item.status)
        # 图标模式：序号 + 动作名 + 状态
        display_text = f"{index + 1}. {item.definition.name} [{status_text}]"
        list_item.setText(display_text)
        list_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        list_item.setToolTip(f"{item.definition.name}\n状态: {status_text}\n参数: {item.definition.parameters}")

        # 透明背景
        list_item.setBackground(Qt.GlobalColor.transparent)

        # 大卡片图标
        icon = self._create_text_icon(item.definition.name, item.definition.type, item.status, index)
        list_item.setIcon(icon)

    def _get_status_text(self, status: SequenceItemStatus) -> str:
        text_map = {
            SequenceItemStatus.PENDING: "等待中",
            SequenceItemStatus.RUNNING: "执行中",
            SequenceItemStatus.SUCCESS: "完成",
            SequenceItemStatus.FAILED: "失败"
        }
        return text_map.get(status, "未知")

    def _create_small_icon(self, action_type: ActionType, status: SequenceItemStatus) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colors = {
            ActionType.MOVE: QColor(100, 149, 237),  # 机械臂移动 - 蓝色
            ActionType.BASE_MOVE: QColor(255, 99, 71),  # 底盘移动 - 红色
            ActionType.MANIPULATE: QColor(255, 140, 0),
            ActionType.WAIT: QColor(255, 140, 0),
            ActionType.INSPECT: QColor(60, 179, 113),
            ActionType.CHANGE_GUN: QColor(147, 112, 219),
            ActionType.VISION_CAPTURE: QColor(30, 144, 255),
        }

        if status == SequenceItemStatus.RUNNING:
            color = QColor(255, 165, 0)
        elif status == SequenceItemStatus.SUCCESS:
            color = QColor(180, 180, 180)
        elif status == SequenceItemStatus.FAILED:
            color = QColor(244, 67, 54)
        else:
            color = colors.get(action_type, QColor(128, 128, 128))

        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(1, 1, 18, 18, 4, 4)
        painter.end()
        return QIcon(pixmap)

    def _create_text_icon(self, text: str, action_type: ActionType, status: SequenceItemStatus, index: int) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor, QPen
        from PyQt6.QtCore import QRectF

        width, height = 120, 80
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colors = {
            ActionType.MOVE: QColor(100, 149, 237),  # 机械臂移动 - 蓝色
            ActionType.BASE_MOVE: QColor(255, 99, 71),  # 底盘移动 - 红色
            ActionType.MANIPULATE: QColor(255, 140, 0),
            ActionType.WAIT: QColor(255, 140, 0),
            ActionType.INSPECT: QColor(60, 179, 113),
            ActionType.CHANGE_GUN: QColor(147, 112, 219),
            ActionType.VISION_CAPTURE: QColor(30, 144, 255),
        }

        if status == SequenceItemStatus.RUNNING:
            painter.setBrush(QColor(255, 165, 0))
            pen = QPen(QColor(0, 255, 0), 4)
            painter.setPen(pen)
        elif status == SequenceItemStatus.SUCCESS:
            painter.setBrush(QColor(180, 180, 180))
            painter.setPen(Qt.PenStyle.NoPen)
        elif status == SequenceItemStatus.FAILED:
            painter.setBrush(QColor(244, 67, 54))
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setBrush(colors.get(action_type, QColor(128, 128, 128)))
            painter.setPen(Qt.PenStyle.NoPen)

        painter.drawRoundedRect(4, 4, width - 8, height - 8, 8, 8)

        # 序号
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)
        font.setPointSize(18)
        painter.setFont(font)
        painter.drawText(QRectF(4, 4, width - 8, 32),
                         Qt.AlignmentFlag.AlignLeft, f"#{index + 1}")

        # 动作名（截断）
        font.setPointSize(10)
        font.setBold(False)
        painter.setFont(font)
        truncated_text = text[:8] + ".." if len(text) > 8 else text
        painter.drawText(QRectF(4, 34, width - 8, 28),
                         Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                         truncated_text)

        # 状态
        status_text = self._get_status_text(status)
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(0, height - 22, width, 18),
                         Qt.AlignmentFlag.AlignCenter, status_text)

        painter.end()
        return QIcon(pixmap)

    def get_sequence(self) -> list[SequenceItem]:
        sequence = []
        for i in range(self.count()):
            item = self.item(i).data(Qt.ItemDataRole.UserRole)
            sequence.append(item)
        return sequence

    def clear_sequence(self):
        self.clear()


class ControlPanel(QWidget):
    start_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    move_up_clicked = pyqtSignal()
    move_down_clicked = pyqtSignal()
    edit_clicked = pyqtSignal()
    delete_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    save_clicked = pyqtSignal()
    load_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        # 竖屏适配：全竖向堆叠，无多余标签
        layout = QVBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(2, 2, 2, 2)

        # 序列编辑按钮（两行：上移/下移 + 删除/清空）
        edit_row1 = QHBoxLayout()
        edit_row1.setSpacing(4)
        self.up_btn = QPushButton("上移")
        self.up_btn.setMinimumHeight(28)
        self.up_btn.clicked.connect(self.move_up_clicked.emit)
        self.down_btn = QPushButton("下移")
        self.down_btn.setMinimumHeight(28)
        self.down_btn.clicked.connect(self.move_down_clicked.emit)
        self.edit_btn = QPushButton("修改")
        self.edit_btn.setMinimumHeight(28)
        self.edit_btn.clicked.connect(self.edit_clicked.emit)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setMinimumHeight(28)
        self.delete_btn.clicked.connect(self.delete_clicked.emit)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setMinimumHeight(28)
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        edit_row1.addWidget(self.up_btn)
        edit_row1.addWidget(self.down_btn)
        edit_row1.addWidget(self.edit_btn)
        edit_row1.addWidget(self.delete_btn)
        edit_row1.addWidget(self.clear_btn)
        layout.addLayout(edit_row1)

        # 保存/载入按钮
        save_load_row = QHBoxLayout()
        save_load_row.setSpacing(4)
        self.save_btn = QPushButton("保存序列")
        self.save_btn.setMinimumHeight(28)
        self.save_btn.setStyleSheet("background-color: #2196F3; color: white;")
        self.save_btn.clicked.connect(self.save_clicked.emit)
        self.load_btn = QPushButton("载入序列")
        self.load_btn.setMinimumHeight(28)
        self.load_btn.setStyleSheet("background-color: #2196F3; color: white;")
        self.load_btn.clicked.connect(self.load_clicked.emit)
        save_load_row.addWidget(self.save_btn)
        save_load_row.addWidget(self.load_btn)
        layout.addLayout(save_load_row)

        # 执行控制按钮（两行：开始/暂停 + 紧急停止）
        exec_row1 = QHBoxLayout()
        exec_row1.setSpacing(4)
        self.start_btn = QPushButton("开始执行")
        self.start_btn.setMinimumHeight(32)
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.start_btn.clicked.connect(self.start_clicked.emit)
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumHeight(32)
        self.pause_btn.setStyleSheet("background-color: #FFC107; color: black; font-weight: bold;")
        self.pause_btn.clicked.connect(self.pause_clicked.emit)
        exec_row1.addWidget(self.start_btn)
        exec_row1.addWidget(self.pause_btn)
        layout.addLayout(exec_row1)

        self.stop_btn = QPushButton("紧急停止")
        self.stop_btn.setMinimumHeight(32)
        self.stop_btn.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;")
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.stop_btn)

        self.setLayout(layout)


class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(120)
        self.setStyleSheet("font-family: Consolas, Monaco, monospace; font-size: 12px;")

    def append_log(self, message: str):
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.append(f"[{timestamp}] {message}")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
