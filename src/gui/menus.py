"""Cross-platform menu widgets with deterministic popup geometry."""

from __future__ import annotations

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QMenu, QWidget


SUBMENU_GAP = 4


class PositionedSubMenu(QMenu):
    """Keep a submenu beside, rather than on top of, its parent menu."""

    def __init__(
        self,
        title: str,
        parent: QWidget,
        *,
        gap: int = SUBMENU_GAP,
    ) -> None:
        super().__init__(title, parent)
        self._gap = max(0, gap)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._position_beside_parent()

    def _position_beside_parent(self) -> None:
        parent_menu = self.parentWidget()
        if not isinstance(parent_menu, QMenu):
            return

        parent_geometry = parent_menu.frameGeometry()
        available = parent_menu.screen().availableGeometry()
        right_x = parent_geometry.right() + 1 + self._gap
        left_x = parent_geometry.left() - self.width() - self._gap
        if right_x + self.width() - 1 <= available.right():
            target_x = right_x
        else:
            target_x = max(available.left(), left_x)

        action_rect = parent_menu.actionGeometry(self.menuAction())
        target_y = parent_menu.mapToGlobal(action_rect.topLeft()).y()
        target_y = max(
            available.top(),
            min(target_y, available.bottom() - self.height() + 1),
        )
        self.move(target_x, target_y)
