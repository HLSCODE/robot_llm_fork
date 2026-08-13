"""Categorized action picker used by workflow insertion targets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...domain.models import ActionDefinition, ActionType
from .action_list import ACTION_TYPE_LABELS


_LIST_ITEM_VERTICAL_PADDING = 12
_LIST_ITEM_MINIMUM_HEIGHT = 28


def _create_list_item(
    list_widget: QListWidget,
    text: str,
) -> QListWidgetItem:
    """Create an item whose layout includes stylesheet padding on every platform."""
    item = QListWidgetItem(text)
    line_height = list_widget.fontMetrics().lineSpacing()
    item.setSizeHint(
        QSize(
            0,
            max(
                _LIST_ITEM_MINIMUM_HEIGHT,
                line_height + _LIST_ITEM_VERTICAL_PADDING,
            ),
        )
    )
    return item


class ActionPickerDialog(QDialog):
    """Select one action through a stable category/action master-detail view."""

    def __init__(
        self,
        actions_by_type: Mapping[ActionType, Sequence[ActionDefinition]],
        *,
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 420)
        self._actions_by_type = {
            action_type: tuple(actions)
            for action_type, actions in actions_by_type.items()
            if actions
        }

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)
        root_layout.addWidget(QLabel("先选择动作类型，再选择要插入的动作"))

        lists_layout = QHBoxLayout()
        lists_layout.setSpacing(12)
        self.category_list = QListWidget()
        self.category_list.setObjectName("actionPickerCategoryList")
        self.category_list.setAccessibleName("动作类型")
        self.category_list.setMinimumWidth(170)
        self.action_list = QListWidget()
        self.action_list.setObjectName("actionPickerActionList")
        self.action_list.setAccessibleName("分类内动作")
        for list_widget in (self.category_list, self.action_list):
            list_widget.setUniformItemSizes(True)
            list_widget.setVerticalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel
            )
        lists_layout.addWidget(self.category_list, stretch=1)
        lists_layout.addWidget(self.action_list, stretch=2)
        root_layout.addLayout(lists_layout, stretch=1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        root_layout.addWidget(self.buttons)

        self.category_list.currentItemChanged.connect(
            self._render_current_category
        )
        self.action_list.itemDoubleClicked.connect(
            lambda _item: self._accept_selection()
        )
        self._render_categories()

    @property
    def selected_action(self) -> ActionDefinition | None:
        item = self.action_list.currentItem()
        if item is None:
            return None
        action = item.data(Qt.ItemDataRole.UserRole)
        return action if isinstance(action, ActionDefinition) else None

    @classmethod
    def choose(
        cls,
        actions_by_type: Mapping[ActionType, Sequence[ActionDefinition]],
        *,
        title: str,
        parent: QWidget | None = None,
    ) -> ActionDefinition | None:
        dialog = cls(actions_by_type, title=title, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_action

    def _render_categories(self) -> None:
        for action_type, label in ACTION_TYPE_LABELS.items():
            actions = self._actions_by_type.get(action_type, ())
            if not actions:
                continue
            item = _create_list_item(
                self.category_list,
                f"{label}  ({len(actions)})",
            )
            item.setData(Qt.ItemDataRole.UserRole, action_type)
            self.category_list.addItem(item)
        if self.category_list.count():
            self.category_list.setCurrentRow(0)

    def _render_current_category(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        self.action_list.clear()
        if current is None:
            return
        action_type = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(action_type, ActionType):
            return
        for action in self._actions_by_type.get(action_type, ()):
            item = _create_list_item(self.action_list, action.name)
            item.setData(Qt.ItemDataRole.UserRole, action)
            item.setToolTip(f"{action.name}\n类型：{action.type.value}")
            self.action_list.addItem(item)
        if self.action_list.count():
            self.action_list.setCurrentRow(0)

    def _accept_selection(self) -> None:
        if self.selected_action is not None:
            self.accept()
