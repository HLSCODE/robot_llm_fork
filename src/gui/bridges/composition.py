from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from ...application import CompositionService


class CompositionBridge(QObject):
    """Marshal composition events from service threads to the Qt thread."""

    changed = Signal(object)

    def __init__(
        self,
        composition: CompositionService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._unsubscribe: Callable[[], None] | None = (
            composition.subscribe(self.changed.emit)
        )

    def close(self) -> None:
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()
