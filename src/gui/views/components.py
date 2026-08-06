from PySide6.QtWidgets import (QWidget, QListWidget, QListWidgetItem,
                            QPushButton, QVBoxLayout, QHBoxLayout, QTextEdit,
                            QTreeWidget, QTreeWidgetItem, QMenu, QInputDialog)
from PySide6.QtCore import Qt, Signal, QSize, QMimeData
from PySide6.QtGui import QIcon, QColor, QDrag
import json
from ...domain.models import ActionDefinition, SequenceItem, SequenceItemStatus, ActionType, LoopBlock, SequenceEntry


class ActionListWidget(QListWidget):
    action_selected = Signal(ActionDefinition)

    # ── 动作类型 → (emoji, 颜色) 映射 ──
    _TYPE_STYLE = {
        ActionType.MOVE: ("🦾", QColor(99, 102, 241)),          # indigo
        ActionType.BASE_MOVE: ("🚗", QColor(239, 68, 68)),      # red
        ActionType.MANIPULATE: ("⚡", QColor(249, 115, 22)),     # orange
        ActionType.WAIT: ("⏳", QColor(245, 158, 11)),           # amber
        ActionType.INSPECT: ("🔍", QColor(16, 185, 129)),        # emerald
        ActionType.CHANGE_GUN: ("🔧", QColor(139, 92, 246)),     # violet
        ActionType.VISION_CAPTURE: ("👁", QColor(14, 165, 233)), # sky
        ActionType.VISION_RELOCALIZE: ("📍", QColor(6, 182, 212)), # cyan
        ActionType.TRAJECTORY: ("📐", QColor(20, 184, 166)),     # teal
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setIconSize(QSize(44, 44))
        self.setSpacing(3)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setStyleSheet("""
            QListWidget {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                outline: none;
            }
            QListWidget::item {
                padding: 4px 8px;
                margin: 1px 4px;
                border-radius: 8px;
                border: 1px solid transparent;
                font-size: 12px;
                font-weight: 500;
                color: #1e293b;
            }
            QListWidget::item:hover {
                background: #f8fafc;
                border-color: #e2e8f0;
            }
            QListWidget::item:selected {
                background: #eff6ff;
                border-color: #bfdbfe;
                color: #1e40af;
            }
        """)
        self.itemDoubleClicked.connect(self._emit_selected_action)

    def _emit_selected_action(self, item: QListWidgetItem) -> None:
        action = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(action, ActionDefinition):
            self.action_selected.emit(action)

    def startDrag(self, supportedActions):
        current_item = self.currentItem()
        if current_item:
            action = current_item.data(Qt.ItemDataRole.UserRole)
            if action:
                mime = QMimeData()
                mime.setData("application/x-action", json.dumps(action.to_dict()).encode('utf-8'))

                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.setPixmap(self.currentItem().icon().pixmap(60, 60))
                drag.exec(Qt.DropAction.CopyAction)

    def add_action(self, action: ActionDefinition):
        item = QListWidgetItem()
        item.setText(action.name)
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        item.setSizeHint(QSize(100, 50))

        icon = self._get_icon_for_type(action.type)
        item.setIcon(icon)

        # 丰富 tooltip
        params_preview = ", ".join(
            f"{k}={v}" for k, v in list(action.parameters.items())[:4]
        )
        item.setToolTip(
            f"📌 {action.name}\n"
            f"📂 类型: {action.type.value}\n"
            f"⚙ 参数: {params_preview or '无'}"
        )

        item.setData(Qt.ItemDataRole.UserRole, action)
        self.addItem(item)

    def get_selected_action(self) -> ActionDefinition:
        current = self.currentItem()
        if current:
            return current.data(Qt.ItemDataRole.UserRole)
        return None

    def _get_icon_for_type(self, action_type: ActionType) -> QIcon:
        emoji, color = self._TYPE_STYLE.get(
            action_type, ("📋", QColor(148, 163, 184))
        )
        return self._create_rich_icon(color, emoji)

    def _create_rich_icon(self, color: QColor, emoji: str) -> QIcon:
        """绘制带 emoji + 渐变背景的圆角图标 (44×44)"""
        from PySide6.QtGui import QPixmap, QPainter, QFont, QLinearGradient

        size = 44
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 微渐变背景
        gradient = QLinearGradient(0, 0, size, size)
        lighter = QColor(
            min(255, color.red() + 40),
            min(255, color.green() + 40),
            min(255, color.blue() + 40),
        )
        gradient.setColorAt(0.0, lighter)
        gradient.setColorAt(1.0, color)
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)

        # Emoji 居中
        font = QFont()
        font.setPointSize(18)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        from PySide6.QtCore import QRectF
        painter.drawText(
            QRectF(0, 0, size, size),
            Qt.AlignmentFlag.AlignCenter,
            emoji,
        )

        painter.end()
        return QIcon(pixmap)


class SequenceListWidget(QTreeWidget):
    """序列编辑器 — 基于 QTreeWidget，支持循环块嵌套显示。

    - 顶层节点: SequenceItem（普通动作）或 LoopBlock（循环容器）
    - 循环容器子节点: SequenceItem（循环体内的动作）
    - 通过 UUID 映射实现 O(1) 的树节点查找
    """

    sequence_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.setDragEnabled(False)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setHeaderHidden(True)
        self.setIndentation(24)
        self.setAnimated(True)
        self.setExpandsOnDoubleClick(True)
        self.setIconSize(QSize(130, 88))
        self.setStyleSheet("""
            QTreeWidget {
                background-color: #f8fafc;
                border: 2px dashed #cbd5e1;
                border-radius: 12px;
                padding: 4px;
            }
            QTreeWidget::item {
                border: 2px solid transparent;
                border-radius: 10px;
                padding: 4px 6px;
                font-size: 11px;
                font-weight: bold;
                background: transparent;
                min-height: 44px;
            }
            QTreeWidget::item:hover {
                border-color: #93c5fd;
                background: rgba(59, 130, 246, 0.06);
            }
            QTreeWidget::item:selected {
                border: 2px solid #3b82f6;
                background: rgba(59, 130, 246, 0.10);
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings,
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                border: none;
                background: transparent;
            }
        """)

        # UUID → QTreeWidgetItem 映射（用于 O(1) 状态更新查找）
        self._item_map: dict[str, QTreeWidgetItem] = {}

        # 右键菜单
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # ────────────────── ID mapping helpers ──────────────────

    def _register_item(self, tree_item: QTreeWidgetItem, entry: SequenceEntry):
        """将树节点注册到 UUID 映射表中"""
        self._item_map[entry.uuid] = tree_item

    def _unregister_item(self, entry: SequenceEntry):
        """从 UUID 映射表中移除"""
        self._item_map.pop(entry.uuid, None)

    def _find_item_by_entry(self, entry: SequenceEntry) -> QTreeWidgetItem | None:
        """通过 SequenceItem 或 LoopBlock 的 UUID 查找对应的树节点"""
        return self._item_map.get(entry.uuid)

    # ────────────────── Drag & Drop ──────────────────

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

            # 判断 drop 目标：如果是循环块内，添加到循环块中
            target = self.itemAt(event.position().toPoint())
            target_entry = target.data(0, Qt.ItemDataRole.UserRole) if target else None
            if target_entry and isinstance(target_entry, LoopBlock):
                self._add_child_item(target, sequence_item, target_entry)
            else:
                self.add_sequence_item(sequence_item)
            self.sequence_changed.emit()
            event.accept()
        else:
            event.ignore()

    # ────────────────── Adding items ──────────────────

    def add_sequence_item(self, item: SequenceItem, parent: QTreeWidgetItem | None = None):
        """添加一个 SequenceItem 到序列末尾（或指定父节点下）"""
        tree_item = QTreeWidgetItem()
        current_index = self.topLevelItemCount() if parent is None else parent.childCount()
        self._update_item_display(tree_item, item, current_index)
        tree_item.setData(0, Qt.ItemDataRole.UserRole, item)
        self._register_item(tree_item, item)

        if parent is not None:
            parent.addChild(tree_item)
            parent.setExpanded(True)
            # 更新父循环块的摘要
            loop_entry = parent.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(loop_entry, LoopBlock):
                self._update_loop_display(parent, loop_entry)
        else:
            self.addTopLevelItem(tree_item)

    def add_loop_block(self, loop: LoopBlock):
        """添加一个循环块到序列末尾"""
        tree_item = QTreeWidgetItem()
        self._update_loop_display(tree_item, loop)
        tree_item.setData(0, Qt.ItemDataRole.UserRole, loop)
        tree_item.setFlags(tree_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self._register_item(tree_item, loop)

        # 添加子项
        for i, child_item in enumerate(loop.items):
            child_tree_item = QTreeWidgetItem()
            self._update_item_display(child_tree_item, child_item, i)
            child_tree_item.setData(0, Qt.ItemDataRole.UserRole, child_item)
            self._register_item(child_tree_item, child_item)
            tree_item.addChild(child_tree_item)

        tree_item.setExpanded(True)
        self.addTopLevelItem(tree_item)

    def _add_child_item(self, parent_tree: QTreeWidgetItem, item: SequenceItem, loop_entry: LoopBlock):
        """向已有循环块内添加子动作"""
        loop_entry.items.append(item)
        child_tree = QTreeWidgetItem()
        idx = parent_tree.childCount()
        self._update_item_display(child_tree, item, idx)
        child_tree.setData(0, Qt.ItemDataRole.UserRole, item)
        self._register_item(child_tree, item)
        parent_tree.addChild(child_tree)
        parent_tree.setExpanded(True)
        self._update_loop_display(parent_tree, loop_entry)

    # ────────────────── Wrapping / Unwrapping loops ──────────────────

    def wrap_in_loop(self, items: list[SequenceItem], repeat_count: int) -> LoopBlock:
        """将一组 SequenceItem 包裹为 LoopBlock（内部会克隆子项）"""
        loop = LoopBlock.from_sequence_items(items, repeat_count)
        self.add_loop_block(loop)
        self.sequence_changed.emit()
        return loop

    def unwrap_loop(self, loop_tree_item: QTreeWidgetItem) -> list[SequenceItem]:
        """展开循环块 — 移除循环容器，将子动作升为顶层"""
        loop_entry = loop_tree_item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(loop_entry, LoopBlock):
            return []

        idx = self.indexOfTopLevelItem(loop_tree_item)
        if idx < 0:
            return []

        # 克隆子项
        children = [SequenceItem.from_dict(s.to_dict()) for s in loop_entry.items]

        # 移除循环块（这会删除子节点）
        self._unregister_item(loop_entry)
        for child in loop_entry.items:
            self._unregister_item(child)
        self.takeTopLevelItem(idx)

        # 在相同位置插入展开的子动作
        for offset, child in enumerate(children):
            child_tree = QTreeWidgetItem()
            self._update_item_display(child_tree, child, idx + offset)
            child_tree.setData(0, Qt.ItemDataRole.UserRole, child)
            self._register_item(child_tree, child)
            self.insertTopLevelItem(idx + offset, child_tree)

        self.sequence_changed.emit()
        return children

    def edit_loop_count(self, loop_tree_item: QTreeWidgetItem, new_count: int):
        """修改循环块的重复次数"""
        loop_entry = loop_tree_item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(loop_entry, LoopBlock):
            return
        loop_entry.repeat_count = max(1, new_count)
        loop_entry.current_iteration = 0
        self._update_loop_display(loop_tree_item, loop_entry)
        self.sequence_changed.emit()

    # ────────────────── Context menu ──────────────────

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return

        entry = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)

        if isinstance(entry, LoopBlock):
            edit_action = menu.addAction("✏ 修改循环次数")
            unwrap_action = menu.addAction("📤 展开循环（取消循环）")
            delete_action = menu.addAction("🗑 删除循环块")
            menu.addSeparator()
            collapse_action = menu.addAction(
                "▶ 折叠" if item.isExpanded() else "▼ 展开"
            )

            action = menu.exec(self.viewport().mapToGlobal(pos))
            if action == edit_action:
                new_count, ok = QInputDialog.getInt(
                    self, "修改循环次数", "循环次数:",
                    entry.repeat_count, 1, 999, 1
                )
                if ok:
                    self.edit_loop_count(item, new_count)
            elif action == unwrap_action:
                self.unwrap_loop(item)
            elif action == delete_action:
                self._remove_loop_block(item)
            elif action == collapse_action:
                item.setExpanded(not item.isExpanded())
        elif isinstance(entry, SequenceItem):
            # 判断是在循环内还是顶层
            parent_item = item.parent()
            if parent_item is not None:
                parent_entry = parent_item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(parent_entry, LoopBlock):
                    remove_action = menu.addAction("🗑 从循环中移除")
                    action = menu.exec(self.viewport().mapToGlobal(pos))
                    if action == remove_action:
                        self._remove_child_from_loop(parent_item, item, entry, parent_entry)
            else:
                # 顶层动作 - 可以包装为循环
                loop_action = menu.addAction("🔁 包装为循环")
                delete_action = menu.addAction("🗑 删除")
                action = menu.exec(self.viewport().mapToGlobal(pos))
                if action == loop_action:
                    repeat_count, ok = QInputDialog.getInt(
                        self, "包装为循环", "循环次数:", 2, 1, 999, 1
                    )
                    if ok:
                        # 需要从 main_window 协调……这里仅发出信号
                        pass
                elif action == delete_action:
                    self._remove_top_level_item(item, entry)

    def _remove_top_level_item(self, tree_item: QTreeWidgetItem, entry: SequenceEntry):
        idx = self.indexOfTopLevelItem(tree_item)
        if idx >= 0:
            self._unregister_item(entry)
            self.takeTopLevelItem(idx)
            self.sequence_changed.emit()

    def _remove_loop_block(self, loop_tree_item: QTreeWidgetItem):
        loop_entry = loop_tree_item.data(0, Qt.ItemDataRole.UserRole)
        idx = self.indexOfTopLevelItem(loop_tree_item)
        if idx >= 0:
            self._unregister_item(loop_entry)
            for child in loop_entry.items:
                self._unregister_item(child)
            self.takeTopLevelItem(idx)
            self.sequence_changed.emit()

    def _remove_child_from_loop(self, parent_tree: QTreeWidgetItem,
                                  child_tree: QTreeWidgetItem,
                                  child_entry: SequenceItem,
                                  loop_entry: LoopBlock):
        idx = parent_tree.indexOfChild(child_tree)
        if idx >= 0:
            self._unregister_item(child_entry)
            parent_tree.takeChild(idx)
            loop_entry.items = [s for s in loop_entry.items if s.uuid != child_entry.uuid]
            self._update_loop_display(parent_tree, loop_entry)
            self.sequence_changed.emit()

    # ────────────────── Display updates ──────────────────

    def update_item_status(self, item: SequenceItem):
        """通过 SequenceItem 的 UUID 查找树节点并更新显示"""
        tree_item = self._find_item_by_entry(item)
        if tree_item is None:
            return
        parent = tree_item.parent()
        if parent is not None:
            idx = parent.indexOfChild(tree_item)
        else:
            idx = self.indexOfTopLevelItem(tree_item)
        if idx < 0:
            idx = 0
        self._update_item_display(tree_item, item, idx)

    def update_loop_status(self, loop: LoopBlock):
        """更新循环块父节点显示（如执行进度）"""
        tree_item = self._find_item_by_entry(loop)
        if tree_item is None:
            return
        self._update_loop_display(tree_item, loop)

    def _update_item_display(self, tree_item: QTreeWidgetItem, item: SequenceItem, index: int):
        status_text = self._get_status_text(item.status)
        tree_item.setText(0, f"{item.definition.name}  [{status_text}]")
        tree_item.setToolTip(0, f"{item.definition.name}\n状态: {status_text}\n参数: {item.definition.parameters}")
        tree_item.setBackground(0, Qt.GlobalColor.transparent)
        icon = self._create_text_icon(item.definition.name, item.definition.type, item.status, index)
        tree_item.setIcon(0, icon)
        # 存储 item 引用以确保 UUID 映射一致
        tree_item.setData(0, Qt.ItemDataRole.UserRole, item)

    def _update_loop_display(self, tree_item: QTreeWidgetItem, loop: LoopBlock):
        """更新循环块父节点的显示文本"""
        child_count = len(loop.items)
        total = child_count * loop.repeat_count
        if loop.current_iteration > 0:
            progress = f" 第{loop.current_iteration}/{loop.repeat_count}轮"
        else:
            progress = ""
        tree_item.setText(0, f"🔁 循环 ×{loop.repeat_count}  ({child_count}个动作 × {loop.repeat_count}次 = {total}步){progress}")
        tree_item.setToolTip(0, f"循环块\n子动作: {child_count} 个\n循环次数: {loop.repeat_count}\n总步数: {total}")
        # 循环块使用特殊的紫色图标
        icon = self._create_loop_icon(child_count, loop.repeat_count, loop.current_iteration)
        tree_item.setIcon(0, icon)
        tree_item.setData(0, Qt.ItemDataRole.UserRole, loop)

    @staticmethod
    def _get_status_text(status: SequenceItemStatus) -> str:
        text_map = {
            SequenceItemStatus.PENDING: "⏳ 等待中",
            SequenceItemStatus.RUNNING: "▶ 执行中",
            SequenceItemStatus.SUCCESS: "✅ 完成",
            SequenceItemStatus.FAILED: "❌ 失败"
        }
        return text_map.get(status, "未知")

    # ────────────────── Card icons ──────────────────

    _CARD_STYLE = {
        ActionType.MOVE: ("🦾", QColor(99, 102, 241)),
        ActionType.BASE_MOVE: ("🚗", QColor(239, 68, 68)),
        ActionType.MANIPULATE: ("⚡", QColor(249, 115, 22)),
        ActionType.WAIT: ("⏳", QColor(245, 158, 11)),
        ActionType.INSPECT: ("🔍", QColor(16, 185, 129)),
        ActionType.CHANGE_GUN: ("🔧", QColor(139, 92, 246)),
        ActionType.VISION_CAPTURE: ("👁", QColor(14, 165, 233)),
        ActionType.VISION_RELOCALIZE: ("📍", QColor(6, 182, 212)),
        ActionType.TRAJECTORY: ("📐", QColor(20, 184, 166)),
    }

    _TYPE_LABELS = {
        ActionType.MOVE: "机械臂移动",
        ActionType.BASE_MOVE: "底盘移动",
        ActionType.MANIPULATE: "执行器",
        ActionType.WAIT: "等待",
        ActionType.INSPECT: "检测",
        ActionType.CHANGE_GUN: "换枪",
        ActionType.VISION_CAPTURE: "视觉抓取",
        ActionType.VISION_RELOCALIZE: "视觉重定位",
        ActionType.TRAJECTORY: "轨迹",
    }

    def _create_text_icon(self, text: str, action_type: ActionType, status: SequenceItemStatus, index: int | None = None) -> QIcon:
        from PySide6.QtGui import QPixmap, QPainter, QFont, QColor, QPen
        from PySide6.QtCore import QRectF

        width, height = 130, 88
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        emoji, base_color = self._CARD_STYLE.get(
            action_type, ("📋", QColor(148, 163, 184))
        )

        if status == SequenceItemStatus.RUNNING:
            fill_color = QColor(251, 191, 36)
            border_color = QColor(34, 197, 94)
            emoji = "▶"
        elif status == SequenceItemStatus.SUCCESS:
            fill_color = QColor(148, 163, 184)
            border_color = None
        elif status == SequenceItemStatus.FAILED:
            fill_color = QColor(239, 68, 68)
            border_color = None
        else:
            fill_color = base_color
            border_color = None

        card_rect = QRectF(3, 3, width - 6, height - 6)
        painter.setBrush(fill_color)
        if border_color:
            painter.setPen(QPen(border_color, 3))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(card_rect, 10, 10)

        if status not in (SequenceItemStatus.SUCCESS,):
            highlight = QColor(255, 255, 255, 45)
            painter.setBrush(highlight)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(5, 5, width - 10, 16), 6, 6)

        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        header_text = f"#{index + 1}" if index is not None else emoji
        painter.drawText(QRectF(8, 2, width - 16, 28), Qt.AlignmentFlag.AlignLeft, header_text)

        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, width - 8, 30), Qt.AlignmentFlag.AlignRight, emoji)

        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        truncated = text[:14] + "…" if len(text) > 14 else text
        painter.drawText(
            QRectF(8, 30, width - 16, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            truncated,
        )

        type_label = self._TYPE_LABELS.get(action_type, action_type.value)
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(QRectF(8, 50, width - 16, 16), Qt.AlignmentFlag.AlignLeft, type_label)

        status_text = self._get_status_text(status)
        status_bg = QColor(0, 0, 0, 40)
        painter.setBrush(status_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(5, height - 26, width - 10, 22), 6, 6)

        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        if status == SequenceItemStatus.RUNNING:
            painter.setPen(QColor(255, 255, 255))
        elif status == SequenceItemStatus.SUCCESS:
            painter.setPen(QColor(220, 220, 220))
        elif status == SequenceItemStatus.FAILED:
            painter.setPen(QColor(255, 220, 220))
        else:
            painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRectF(0, height - 26, width, 22), Qt.AlignmentFlag.AlignCenter, status_text)

        painter.end()
        return QIcon(pixmap)

    def _create_loop_icon(self, child_count: int, repeat_count: int, current_iteration: int = 0) -> QIcon:
        from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
        from PySide6.QtCore import QRectF

        width, height = 130, 88
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 循环块用紫色渐变
        loop_color = QColor(139, 92, 246)
        painter.setBrush(loop_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(3, 3, width - 6, height - 6, 10, 10)

        # 顶部高光
        highlight = QColor(255, 255, 255, 45)
        painter.setBrush(highlight)
        painter.drawRoundedRect(QRectF(5, 5, width - 10, 16), 6, 6)

        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setBold(True)

        # 右上角 emoji
        font.setPointSize(14)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, width - 8, 30), Qt.AlignmentFlag.AlignRight, "🔁")

        # 循环次数
        font.setPointSize(13)
        painter.setFont(font)
        painter.drawText(QRectF(8, 16, width - 16, 30), Qt.AlignmentFlag.AlignLeft, f"×{repeat_count}")

        # 信息
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(
            QRectF(8, 44, width - 16, 16),
            Qt.AlignmentFlag.AlignLeft,
            f"{child_count}个动作",
        )

        total = child_count * repeat_count
        painter.drawText(
            QRectF(8, 58, width - 16, 16),
            Qt.AlignmentFlag.AlignLeft,
            f"共{total}步",
        )

        # 底部状态
        status_bg = QColor(0, 0, 0, 40)
        painter.setBrush(status_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(5, height - 26, width - 10, 22), 6, 6)

        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        status_text = f"第{current_iteration}/{repeat_count}轮" if current_iteration > 0 else "循环容器"
        painter.drawText(QRectF(0, height - 26, width, 22), Qt.AlignmentFlag.AlignCenter, status_text)

        painter.end()
        return QIcon(pixmap)

    # ────────────────── Data access ──────────────────

    def get_sequence(self) -> list[SequenceItem]:
        """返回扁平化序列（LoopBlock 展开为重复的 SequenceItem，用于执行）"""
        sequence: list[SequenceItem] = []
        for i in range(self.topLevelItemCount()):
            entry = self.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, LoopBlock):
                for _ in range(entry.repeat_count):
                    for child in entry.items:
                        sequence.append(SequenceItem.from_dict(child.to_dict()))
            elif isinstance(entry, SequenceItem):
                sequence.append(entry)
        return sequence

    def get_entries(self) -> list[SequenceEntry]:
        """返回混合列表（含 LoopBlock，用于序列化保存）"""
        entries: list[SequenceEntry] = []
        for i in range(self.topLevelItemCount()):
            tree_item = self.topLevelItem(i)
            entry = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, LoopBlock):
                # 同步子项数据
                entry.items = []
                for j in range(tree_item.childCount()):
                    child_entry = tree_item.child(j).data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(child_entry, SequenceItem):
                        entry.items.append(child_entry)
                entries.append(entry)
            elif isinstance(entry, SequenceItem):
                entries.append(entry)
        return entries

    def clear_sequence(self):
        self._item_map.clear()
        self.clear()

    # ────────────────── Top-level entry operations ──────────────────

    def entry_count(self) -> int:
        return self.topLevelItemCount()

    def current_entry_row(self) -> int:
        current = self.currentItem()
        if current is None:
            return -1
        # 如果在某个父节点下，返回父节点索引
        parent = current.parent()
        if parent is not None:
            return self.indexOfTopLevelItem(parent)
        return self.indexOfTopLevelItem(current)

    def take_entry(self, index: int) -> QTreeWidgetItem:
        item = self.takeTopLevelItem(index)
        if item is not None:
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, LoopBlock):
                self._unregister_item(entry)
                for j in range(item.childCount()):
                    child = item.child(j)
                    child_entry = child.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(child_entry, SequenceItem):
                        self._unregister_item(child_entry)
            elif isinstance(entry, SequenceItem):
                self._unregister_item(entry)
        return item

    def insert_entry(self, index: int, tree_item: QTreeWidgetItem) -> None:
        self.insertTopLevelItem(index, tree_item)
        # 重新注册移动后带回的项
        entry = tree_item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(entry, LoopBlock):
            self._register_item(tree_item, entry)
            for j in range(tree_item.childCount()):
                child = tree_item.child(j)
                child_entry = child.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(child_entry, SequenceItem):
                    self._register_item(child, child_entry)
        elif isinstance(entry, SequenceItem):
            self._register_item(tree_item, entry)

    def set_current_entry_row(self, index: int) -> None:
        item = self.topLevelItem(index)
        if item:
            self.setCurrentItem(item)

    def selected_entry_indexes(self) -> list:
        """Return model indexes for selected top-level entries."""
        indexes = []
        for i in range(self.topLevelItemCount()):
            if self.topLevelItem(i).isSelected():
                indexes.append(self.model().index(i, 0))
        return indexes

    def scroll_to_entry(self, tree_item: QTreeWidgetItem) -> None:
        """Scroll to a sequence tree entry."""
        if tree_item is not None:
            super().scrollToItem(tree_item)


class ControlPanel(QWidget):
    start_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    quick_stop_clicked = Signal()
    emergency_stop_clicked = Signal()
    move_up_clicked = Signal()
    move_down_clicked = Signal()
    edit_clicked = Signal()
    repeat_clicked = Signal()
    delete_clicked = Signal()
    clear_clicked = Signal()
    save_clicked = Signal()
    load_clicked = Signal()
    undo_clicked = Signal()
    redo_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(0, 0, 0, 0)

        # History and order row
        edit_row1 = QHBoxLayout()
        edit_row1.setSpacing(4)
        self.undo_btn = QPushButton("↶ 撤销")
        self.redo_btn = QPushButton("↷ 重做")
        for button, slot in (
            (self.undo_btn, self.undo_clicked.emit),
            (self.redo_btn, self.redo_clicked.emit),
        ):
            button.setMinimumHeight(32)
            button.setEnabled(False)
            button.clicked.connect(slot)
            edit_row1.addWidget(button)
        for label, slot in [
            ("↑ 上移", self.move_up_clicked.emit),
            ("↓ 下移", self.move_down_clicked.emit),
        ]:
            btn = QPushButton(label)
            btn.setMinimumHeight(32)
            btn.clicked.connect(slot)
            edit_row1.addWidget(btn)
        layout.addLayout(edit_row1)

        edit_row2 = QHBoxLayout()
        edit_row2.setSpacing(4)
        for label, slot in [
            ("✏ 修改", self.edit_clicked.emit),
            ("🔁 循环", self.repeat_clicked.emit),
            ("🗑 删除", self.delete_clicked.emit),
            ("✕ 清空", self.clear_clicked.emit),
        ]:
            btn = QPushButton(label)
            btn.setMinimumHeight(32)
            btn.clicked.connect(slot)
            edit_row2.addWidget(btn)
        layout.addLayout(edit_row2)

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

        self.stop_btn = QPushButton("⏹ 停止任务")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.setAccessibleName("停止任务")
        self.stop_btn.setToolTip("请求当前任务在可中断点停止；不会触发设备硬件急停")
        self.stop_btn.setStyleSheet("""
            QPushButton { background: #ef4444; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #dc2626; }
            QPushButton:pressed { background: #b91c1c; }
        """)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.stop_btn)

        safety_row = QHBoxLayout()
        safety_row.setSpacing(4)
        self.quick_stop_btn = QPushButton("⚡ 快速停止")
        self.quick_stop_btn.setMinimumHeight(34)
        self.quick_stop_btn.setToolTip(
            "向已支持的运动设备发送软件快停；不能替代物理急停"
        )
        self.quick_stop_btn.setStyleSheet("""
            QPushButton { background: #f97316; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #ea580c; }
            QPushButton:pressed { background: #c2410c; }
        """)
        self.quick_stop_btn.clicked.connect(self.quick_stop_clicked.emit)
        self.emergency_stop_btn = QPushButton("🛑 设备急停")
        self.emergency_stop_btn.setMinimumHeight(34)
        self.emergency_stop_btn.setToolTip(
            "向已支持的运动设备发送软件急停；不能替代物理急停回路"
        )
        self.emergency_stop_btn.setStyleSheet("""
            QPushButton { background: #b91c1c; color: #fff; font-weight: 700; border: none; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #991b1b; }
            QPushButton:pressed { background: #7f1d1d; }
        """)
        self.emergency_stop_btn.clicked.connect(
            self.emergency_stop_clicked.emit
        )
        safety_row.addWidget(self.quick_stop_btn)
        safety_row.addWidget(self.emergency_stop_btn)
        layout.addLayout(safety_row)

        self.setLayout(layout)

    def set_undo_redo_enabled(
        self,
        can_undo: bool,
        can_redo: bool,
    ) -> None:
        self.undo_btn.setEnabled(can_undo)
        self.redo_btn.setEnabled(can_redo)


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
