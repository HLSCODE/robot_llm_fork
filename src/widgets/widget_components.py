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
            ActionType.MOVE: QColor(99, 102, 241),        # indigo
            ActionType.BASE_MOVE: QColor(239, 68, 68),    # red
            ActionType.MANIPULATE: QColor(249, 115, 22),  # orange
            ActionType.WAIT: QColor(249, 115, 22),
            ActionType.INSPECT: QColor(16, 185, 129),     # emerald
            ActionType.CHANGE_GUN: QColor(139, 92, 246),  # violet
            ActionType.VISION_CAPTURE: QColor(14, 165, 233),  # sky
            ActionType.TRAJECTORY: QColor(20, 184, 166),  # teal
        }
        color = colors.get(action_type, QColor(148, 163, 184))
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
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # 横向流动：图标模式，每项较大卡片
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setSpacing(12)
        self.setIconSize(QSize(120, 80))
        self.setStyleSheet("""
            QListWidget {
                background-color: #f8fafc;
                border: 2px dashed #e2e8f0;
                border-radius: 10px;
            }
            QListWidget::item {
                border: 2px solid #e2e8f0;
                border-radius: 10px;
                padding: 2px;
                font-size: 11px;
                font-weight: bold;
                background: #ffffff;
            }
            QListWidget::item:hover {
                border-color: #3b82f6;
                background: #eff6ff;
            }
            QListWidget::item:selected {
                border: 2px solid #3b82f6;
                background: #eff6ff;
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
        display_text = f"{item.definition.name} [{status_text}]"
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
            SequenceItemStatus.PENDING: "⏳ 等待中",
            SequenceItemStatus.RUNNING: "▶ 执行中",
            SequenceItemStatus.SUCCESS: "✅ 完成",
            SequenceItemStatus.FAILED: "❌ 失败"
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
            ActionType.TRAJECTORY: QColor(0, 150, 136),
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

    def _create_text_icon(self, text: str, action_type: ActionType, status: SequenceItemStatus, index: int | None = None) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter, QFont, QColor, QPen
        from PyQt6.QtCore import QRectF

        width, height = 120, 80
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        colors = {
            ActionType.MOVE: QColor(99, 102, 241),        # indigo-500
            ActionType.BASE_MOVE: QColor(239, 68, 68),    # red-500
            ActionType.MANIPULATE: QColor(249, 115, 22),  # orange-500
            ActionType.WAIT: QColor(249, 115, 22),
            ActionType.INSPECT: QColor(16, 185, 129),     # emerald-500
            ActionType.CHANGE_GUN: QColor(139, 92, 246),  # violet-500
            ActionType.VISION_CAPTURE: QColor(14, 165, 233),  # sky-500
            ActionType.TRAJECTORY: QColor(20, 184, 166),  # teal-500
        }

        if status == SequenceItemStatus.RUNNING:
            painter.setBrush(QColor(251, 191, 36))  # amber-400
            pen = QPen(QColor(34, 197, 94), 4)      # green-500
            painter.setPen(pen)
        elif status == SequenceItemStatus.SUCCESS:
            painter.setBrush(QColor(148, 163, 184))  # slate-400
            painter.setPen(Qt.PenStyle.NoPen)
        elif status == SequenceItemStatus.FAILED:
            painter.setBrush(QColor(239, 68, 68))    # red-500
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setBrush(colors.get(action_type, QColor(148, 163, 184)))
            painter.setPen(Qt.PenStyle.NoPen)

        painter.drawRoundedRect(4, 4, width - 8, height - 8, 8, 8)

        # Card number
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)
        font.setPointSize(18)
        painter.setFont(font)
        num_text = f"#{index + 1}" if index is not None else ""
        painter.drawText(QRectF(6, 5, width - 12, 26),
                         Qt.AlignmentFlag.AlignLeft, num_text)

        # Action name
        font.setPointSize(10)
        font.setBold(False)
        painter.setFont(font)
        truncated_text = text[:12] + ".." if len(text) > 12 else text
        painter.drawText(QRectF(6, 32, width - 12, 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                         truncated_text)

        # Status
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
    repeat_clicked = pyqtSignal()
    delete_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    save_clicked = pyqtSignal()
    load_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(0, 0, 0, 0)

        # Sequence edit row
        edit_row1 = QHBoxLayout()
        edit_row1.setSpacing(4)
        for label, slot in [("↑ 上移", self.move_up_clicked.emit),
                            ("↓ 下移", self.move_down_clicked.emit),
                            ("✏ 修改", self.edit_clicked.emit),
                            ("🔁 循环", self.repeat_clicked.emit),
                            ("🗑 删除", self.delete_clicked.emit),
                            ("✕ 清空", self.clear_clicked.emit)]:
            btn = QPushButton(label)
            btn.setMinimumHeight(30)
            btn.clicked.connect(slot)
            edit_row1.addWidget(btn)
        layout.addLayout(edit_row1)

        # Save/Load row
        save_load_row = QHBoxLayout()
        save_load_row.setSpacing(4)
        self.save_btn = QPushButton("💾 保存序列")
        self.save_btn.setMinimumHeight(30)
        self.save_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: #fff; font-weight: 600; border: none; border-radius: 6px; }
            QPushButton:hover { background: #2563eb; }
            QPushButton:pressed { background: #1d4ed8; }
        """)
        self.save_btn.clicked.connect(self.save_clicked.emit)
        self.load_btn = QPushButton("📂 载入序列")
        self.load_btn.setMinimumHeight(30)
        self.load_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: #fff; font-weight: 600; border: none; border-radius: 6px; }
            QPushButton:hover { background: #2563eb; }
            QPushButton:pressed { background: #1d4ed8; }
        """)
        self.load_btn.clicked.connect(self.load_clicked.emit)
        save_load_row.addWidget(self.save_btn)
        save_load_row.addWidget(self.load_btn)
        layout.addLayout(save_load_row)

        # Execute row
        exec_row1 = QHBoxLayout()
        exec_row1.setSpacing(4)
        self.start_btn = QPushButton("▶ 开始执行")
        self.start_btn.setMinimumHeight(34)
        self.start_btn.setStyleSheet("""
            QPushButton { background: #22c55e; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #16a34a; }
            QPushButton:pressed { background: #15803d; }
        """)
        self.start_btn.clicked.connect(self.start_clicked.emit)
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setMinimumHeight(34)
        self.pause_btn.setStyleSheet("""
            QPushButton { background: #f59e0b; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #d97706; }
            QPushButton:pressed { background: #b45309; }
        """)
        self.pause_btn.clicked.connect(self.pause_clicked.emit)
        exec_row1.addWidget(self.start_btn)
        exec_row1.addWidget(self.pause_btn)
        layout.addLayout(exec_row1)

        self.stop_btn = QPushButton("⏹ 紧急停止")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.setStyleSheet("""
            QPushButton { background: #ef4444; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #dc2626; }
            QPushButton:pressed { background: #b91c1c; }
        """)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.stop_btn)

        self.setLayout(layout)


class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(120)
        self.setStyleSheet("""
            QTextEdit {
                font-family: "Cascadia Code", "Consolas", "SF Mono", monospace;
                font-size: 11px;
                background: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 6px 10px;
            }
        """)

    def append_log(self, message: str):
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.append(f'<span style="color:#64748b">[{timestamp}]</span> {message}')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
