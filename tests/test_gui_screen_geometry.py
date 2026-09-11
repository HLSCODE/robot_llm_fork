from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QPoint, QRect
import pytest

from src.gui import screen_geometry


@dataclass(eq=False)
class Screen:
    available: QRect
    valid: bool = True

    def availableGeometry(self) -> QRect:  # noqa: N802
        assert self.valid, "must not query an invalid native screen"
        return self.available


@pytest.fixture
def display(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    gui_thread = object()
    primary = Screen(QRect(0, 0, 1920, 1040))
    secondary = Screen(QRect(-1920, 0, 1920, 1080))
    api = Mock()
    application = api.instance.return_value
    application.thread.return_value = gui_thread
    api.screens.return_value = [primary, secondary]
    api.primaryScreen.return_value = primary
    api.screenAt.return_value = secondary
    core = Mock()
    core.closingDown.return_value = False
    threads = Mock()
    threads.currentThread.return_value = gui_thread
    monkeypatch.setattr(screen_geometry, "QApplication", api)
    monkeypatch.setattr(screen_geometry, "QCoreApplication", core)
    monkeypatch.setattr(screen_geometry, "QThread", threads)
    monkeypatch.setattr(screen_geometry, "isValid", lambda screen: screen.valid)
    return SimpleNamespace(
        api=api, core=core, threads=threads, primary=primary, secondary=secondary,
    )


def test_geometry_uses_global_logical_position_on_secondary_display(display) -> None:
    anchor = QPoint(-1500, 400)
    result = screen_geometry.available_screen_geometry(anchor)
    display.api.screenAt.assert_called_once_with(anchor)
    assert result == display.secondary.available
    display.api.primaryScreen.assert_not_called()
    result.translate(100, 100)
    assert display.secondary.available == QRect(-1920, 0, 1920, 1080)


def test_without_anchor_uses_primary_display(display) -> None:
    assert screen_geometry.available_screen_geometry() == display.primary.available
    display.api.screenAt.assert_not_called()


@pytest.mark.parametrize("selected", [None, "invalid", "removed"])
def test_unavailable_target_falls_back_to_primary(display, selected) -> None:
    if selected is None:
        display.api.screenAt.return_value = None
    else:
        display.api.screenAt.return_value = Screen(QRect(0, 0, 100, 100), selected != "invalid")
    assert screen_geometry.available_screen_geometry(QPoint(9999, 9999)) == (
        display.primary.available
    )


@pytest.mark.parametrize("reason", ["no_app", "closing", "wrong_thread", "no_screens"])
def test_unavailable_gui_does_not_query_screen_geometry(display, reason) -> None:
    if reason == "no_app":
        display.api.instance.return_value = None
    elif reason == "closing":
        display.core.closingDown.return_value = True
    elif reason == "wrong_thread":
        display.threads.currentThread.return_value = object()
    else:
        display.api.screens.return_value = []
    assert screen_geometry.available_screen_geometry(QPoint(0, 0)) is None
    display.api.screenAt.assert_not_called()
    display.api.primaryScreen.assert_not_called()


@pytest.mark.parametrize("primary", [None, "invalid", "removed"])
def test_missing_or_invalid_primary_is_not_dereferenced(display, primary) -> None:
    if primary is None:
        display.api.primaryScreen.return_value = None
    else:
        display.api.primaryScreen.return_value = Screen(QRect(), primary != "invalid")
    assert screen_geometry.available_screen_geometry() is None


def test_invalid_inventory_is_ignored_before_native_screen_lookup(display) -> None:
    display.primary.valid = False
    display.secondary.valid = False
    assert screen_geometry.available_screen_geometry(QPoint()) is None
    display.api.screenAt.assert_not_called()
