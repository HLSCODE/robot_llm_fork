"""Small Qt style overrides required for consistent cross-platform widgets."""

from __future__ import annotations

from typing import TypeGuard

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QStyleHintReturn,
    QStyleOption,
    QWidget,
)


QT_BASE_STYLE_NAME = "Fusion"
COMBO_POPUP_GAP = 4
COMBO_POPUP_RADIUS = 8.0
COMBO_POPUP_COORDINATOR_OBJECT_NAME = "comboBoxPopupCoordinator"

# Qt owns these objects at the C++ level after installation.  Python virtual
# callbacks still require a live Python wrapper, so retain one reference per
# QApplication for the lifetime of the process.
_APPLICATION_STYLES: dict[int, ConsistentWidgetStyle] = {}
_APPLICATION_POPUP_COORDINATORS: dict[int, ComboBoxPopupCoordinator] = {}


class ConsistentWidgetStyle(QProxyStyle):
    """Keep Fusion rendering while making combo boxes behave as drop-downs."""

    def __init__(self) -> None:
        base_style = QStyleFactory.create(QT_BASE_STYLE_NAME)
        if base_style is None:
            raise RuntimeError(f"Qt style is unavailable: {QT_BASE_STYLE_NAME}")
        super().__init__(base_style)
        self.setObjectName(QT_BASE_STYLE_NAME)

    def styleHint(  # noqa: N802
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        return_data: QStyleHintReturn | None = None,
    ) -> int:
        if hint is QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, return_data)


def create_consistent_widget_style() -> ConsistentWidgetStyle:
    return ConsistentWidgetStyle()


def retain_application_style(
    application: QApplication,
    style: ConsistentWidgetStyle,
) -> None:
    """Keep the Python QProxyStyle wrapper alive while Qt calls its overrides."""
    _APPLICATION_STYLES[id(application)] = style


class ComboBoxPopupCoordinator(QObject):
    """Position and clip native combo popups consistently across platforms."""

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self.setObjectName(COMBO_POPUP_COORDINATOR_OBJECT_NAME)
        application.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if _is_combo_popup(watched):
            if event.type() is QEvent.Type.Show:
                self._prepare_popup(watched)
            elif event.type() is QEvent.Type.Resize:
                _apply_rounded_popup_mask(watched)
        return super().eventFilter(watched, event)

    @staticmethod
    def _prepare_popup(popup: QWidget) -> None:
        combo = popup.parentWidget()
        if not isinstance(combo, QComboBox):
            return
        popup.setObjectName("comboBoxPopup")
        _apply_rounded_popup_mask(popup)
        _position_popup_with_gap(combo, popup)


def install_combo_box_popup_coordinator(
    application: QApplication,
) -> ComboBoxPopupCoordinator:
    for child in application.children():
        if (
            isinstance(child, ComboBoxPopupCoordinator)
            and child.objectName() == COMBO_POPUP_COORDINATOR_OBJECT_NAME
        ):
            _APPLICATION_POPUP_COORDINATORS[id(application)] = child
            return child
    coordinator = ComboBoxPopupCoordinator(application)
    _APPLICATION_POPUP_COORDINATORS[id(application)] = coordinator
    return coordinator


def _is_combo_popup(candidate: QObject) -> TypeGuard[QWidget]:
    return (
        isinstance(candidate, QWidget)
        and candidate.metaObject().className() == "QComboBoxPrivateContainer"
    )


def _apply_rounded_popup_mask(popup: QWidget) -> None:
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(popup.rect()),
        COMBO_POPUP_RADIUS,
        COMBO_POPUP_RADIUS,
    )
    popup.setMask(QRegion(path.toFillPolygon().toPolygon()))


def _position_popup_with_gap(combo: QComboBox, popup: QWidget) -> None:
    input_top = combo.mapToGlobal(QPoint(0, 0)).y()
    input_bottom = combo.mapToGlobal(QPoint(0, combo.height())).y()
    popup_geometry = popup.geometry()
    if popup_geometry.top() >= input_bottom - 1:
        target_y = input_bottom + COMBO_POPUP_GAP
    else:
        target_y = input_top - popup.height() - COMBO_POPUP_GAP

    screen = combo.screen()
    available = screen.availableGeometry()
    target_y = max(
        available.top(),
        min(target_y, available.bottom() - popup.height() + 1),
    )
    popup.move(popup_geometry.left(), target_y)
