"""Fixed action categories with one selection and a shared scroll area."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTreeWidget, QTreeWidgetItem, QWidget

from ...domain.models import ActionDefinition, ActionType
from ..icons import IconName
from ..toolbars import IconToolButton
from .action_list import action_library_icon, start_action_drag


ACTION_LIBRARY_CATEGORIES: tuple[tuple[ActionType, str], ...] = (
    (ActionType.MOVE, "移动类"),
    (ActionType.MANIPULATE, "执行类"),
    (ActionType.INSPECT, "检测类"),
    (ActionType.CHANGE_GUN, "换枪类"),
    (ActionType.VISION_CAPTURE, "视觉类"),
    (ActionType.TRAJECTORY, "轨迹类"),
)
_CATEGORY_ALIASES = {
    ActionType.BASE_MOVE: ActionType.MOVE,
    ActionType.WAIT: ActionType.MANIPULATE,
    ActionType.VISION_RELOCALIZE: ActionType.VISION_CAPTURE,
}
_DATA_ROLE = Qt.ItemDataRole.UserRole


class ActionCategoryTree(QTreeWidget):
    action_selected = Signal(object)
    create_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("actionCategoryTree")
        self.setAccessibleName("基础动作分类列表")
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setIndentation(16)
        self.setIconSize(QSize(20, 20))
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setDragDropMode(self.DragDropMode.DragOnly)
        self.setExpandsOnDoubleClick(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 32)
        self.category_items: dict[ActionType, QTreeWidgetItem] = {}
        self.create_buttons: dict[ActionType, IconToolButton] = {}
        self._action_items: dict[str, QTreeWidgetItem] = {}
        self._canvas_scale_provider: Callable[[], float] = lambda: 1.0
        for category, title in ACTION_LIBRARY_CATEGORIES:
            item = QTreeWidgetItem(self, [f"{title}  0"])
            item.setData(0, _DATA_ROLE, category)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            item.setSizeHint(0, QSize(0, 32))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self.category_items[category] = item
            button = IconToolButton(
                IconName.ADD, f"新建{title}动作", hit_size=28, icon_size=16,
                callback=lambda _checked=False, target=category: self._create_in_category(target),
            )
            tools = QWidget()
            tools_layout = QHBoxLayout(tools)
            tools_layout.setContentsMargins(0, 0, 4, 0)
            tools_layout.addWidget(button)
            self.setItemWidget(item, 1, tools)
            button.hide()
            self.create_buttons[category] = button
        self.itemEntered.connect(self._show_category_tools)
        self.itemClicked.connect(self._toggle_category)
        self.itemDoubleClicked.connect(self._insert_action)

    def set_canvas_scale_provider(self, provider: Callable[[], float]) -> None:
        self._canvas_scale_provider = provider

    def selected_action(self) -> ActionDefinition | None:
        item = self.currentItem() if self.selectedItems() else None
        value = item.data(0, _DATA_ROLE) if item is not None else None
        return value if isinstance(value, ActionDefinition) else None

    def current_category(self) -> ActionType | None:
        item = self.currentItem() if self.selectedItems() else None
        value = item.data(0, _DATA_ROLE) if item is not None else None
        if isinstance(value, ActionDefinition):
            return _CATEGORY_ALIASES.get(value.type, value.type)
        return value if isinstance(value, ActionType) else None

    def select_category(self, category: ActionType) -> None:
        self.setCurrentItem(self.category_items[category])

    def reveal_action(self, action_id: str) -> bool:
        item = self._action_items.get(action_id)
        if item is None:
            return False
        parent = item.parent()
        if parent is not None:
            parent.setExpanded(True)
        self.setCurrentItem(item)
        self.scrollToItem(item)
        return True

    def render_actions(self, actions: Sequence[ActionDefinition]) -> None:
        """Refresh children while keeping category expansion, selection and scroll."""
        selected = self.selected_action()
        category = self.current_category()
        scroll = self.verticalScrollBar().value()
        expanded = {key: item.isExpanded() for key, item in self.category_items.items()}
        with QSignalBlocker(self):
            self._action_items.clear()
            for item in self.category_items.values():
                item.takeChildren()
            for action in actions:
                parent = self.category_items[_CATEGORY_ALIASES.get(action.type, action.type)]
                item = QTreeWidgetItem(parent, [action.name])
                item.setData(0, _DATA_ROLE, action)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                item.setFirstColumnSpanned(True)
                item.setSizeHint(0, QSize(0, 36))
                item.setIcon(0, action_library_icon(self, action))
                item.setToolTip(0, f"{action.name}\n类型：{action.type.value}\n双击插入，拖入画布")
                self._action_items[action.id] = item
            for key, title in ACTION_LIBRARY_CATEGORIES:
                parent = self.category_items[key]
                parent.setText(0, f"{title}  {parent.childCount()}")
            current = self._action_items.get(selected.id) if selected else None
            if current is None and category is not None:
                current = self.category_items[category]
            self.setCurrentItem(current)
            for key, item in self.category_items.items():
                item.setExpanded(expanded[key])
            self.verticalScrollBar().setValue(scroll)
        self.itemSelectionChanged.emit()

    def _create_in_category(self, category: ActionType) -> None:
        self.select_category(category)
        self.create_requested.emit()

    def _show_category_tools(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        for category, button in self.create_buttons.items():
            button.setVisible(item is self.category_items[category])

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        super().leaveEvent(event)
        for button in self.create_buttons.values():
            button.hide()

    def _toggle_category(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0 and item.parent() is None:
            item.setExpanded(not item.isExpanded())

    def _insert_action(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        action = item.data(0, _DATA_ROLE)
        if isinstance(action, ActionDefinition):
            self.action_selected.emit(action)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        del supported_actions
        action = self.selected_action()
        if action is not None:
            start_action_drag(self, action, self._canvas_scale_provider())

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            for item in self._action_items.values():
                action = item.data(0, _DATA_ROLE)
                item.setIcon(0, action_library_icon(self, action))
