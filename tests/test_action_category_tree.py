from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.domain.models import ActionDefinition, ActionType
from src.gui.theme import ThemeController, ThemeMode
from src.gui.views.action_tree import ACTION_LIBRARY_CATEGORIES
from src.gui.views.workflow import ActionLibraryView


@pytest.fixture
def library() -> Iterator[ActionLibraryView]:
    application = QApplication.instance() or QApplication([])
    view = ActionLibraryView()
    view.resize(280, 480)
    view.show()
    application.processEvents()
    yield view
    view.close()
    view.deleteLater()
    application.processEvents()


def action(action_type: ActionType, name: str = "测试动作") -> ActionDefinition:
    return ActionDefinition(id=action_type.value, name=name, type=action_type, parameters={})


def test_categories_start_collapsed_and_group_all_action_types(library: ActionLibraryView) -> None:
    library.render_actions([action(kind) for kind in ActionType])
    tree = library.action_tree
    assert list(tree.category_items) == [category for category, _ in ACTION_LIBRARY_CATEGORIES]
    assert [item.childCount() for item in tree.category_items.values()] == [2, 2, 1, 1, 2, 1]
    assert all(not item.isExpanded() for item in tree.category_items.values())
    assert all(button.isHidden() for button in tree.create_buttons.values())
    assert all(str(item.childCount()) in item.text(0) for item in tree.category_items.values())
    assert library.selected_action() is None
    assert not library.edit_button.isEnabled()
    assert not library.delete_button.isEnabled()


def test_empty_selection_prompts_for_category_before_create(library: ActionLibraryView) -> None:
    tree = library.action_tree
    tree.clearSelection()
    requested = []
    library.create_requested.connect(lambda: requested.append(library.current_category_type()))
    library.create_button.click()
    assert requested == []
    assert library.category_menu.isVisible()
    library.category_menu.actions()[4].trigger()
    library.category_menu.close()
    assert requested == [ActionType.VISION_CAPTURE]
    assert tree.currentItem() is tree.category_items[ActionType.VISION_CAPTURE]


def test_selection_drives_commands_even_after_toolbar_takes_focus(library: ActionLibraryView) -> None:
    vision = action(ActionType.VISION_RELOCALIZE)
    library.render_actions([vision])
    library.reveal_action(vision.id)
    assert library.edit_button.isEnabled()
    assert library.delete_button.isEnabled()
    assert library.create_button.toolTip() == "新建视觉类动作"
    requested = []
    library.create_requested.connect(lambda: requested.append(library.current_category_type()))
    library.create_button.setFocus()
    library.create_button.click()
    assert requested == [ActionType.VISION_CAPTURE]
    library.action_tree.select_category(ActionType.MOVE)
    assert library.selected_action() is None
    assert not library.edit_button.isEnabled()
    assert not library.delete_button.isEnabled()
    library.create_button.click()
    assert requested == [ActionType.VISION_CAPTURE, ActionType.MOVE]


def test_category_title_arrow_and_keyboard_expand_without_changing_create_target(
    library: ActionLibraryView,
) -> None:
    library.render_actions([action(ActionType.MOVE)])
    tree = library.action_tree
    category = tree.category_items[ActionType.MOVE]
    rect = tree.visualItemRect(category)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert category.isExpanded()
    assert library.current_category_type() is ActionType.MOVE
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.left() - 8, rect.center().y()))
    assert not category.isExpanded()
    QTest.keyClick(tree, Qt.Key.Key_Right)
    assert category.isExpanded()
    QTest.keyClick(tree, Qt.Key.Key_Left)
    assert not category.isExpanded()
    assert library.current_category_type() is ActionType.MOVE


def test_category_hover_create_uses_its_own_category_without_moving_rows(library: ActionLibraryView) -> None:
    tree = library.action_tree
    tree.select_category(ActionType.MOVE)
    category = tree.category_items[ActionType.TRAJECTORY]
    rect = tree.visualItemRect(category)
    QTest.mouseMove(tree.viewport(), rect.center())
    QApplication.processEvents()
    button = tree.create_buttons[ActionType.TRAJECTORY]
    assert button.isVisible()
    assert tree.visualItemRect(category) == rect
    assert library.current_category_type() is ActionType.MOVE
    requested = []
    library.create_requested.connect(lambda: requested.append(library.current_category_type()))
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert requested == [ActionType.TRAJECTORY]


def test_refresh_preserves_expansion_and_selection_and_removal_clears_action(library: ActionLibraryView) -> None:
    move = action(ActionType.MOVE)
    wait = action(ActionType.WAIT)
    library.render_actions([move, wait])
    library.reveal_action(move.id)
    library.action_tree.category_items[ActionType.MANIPULATE].setExpanded(True)
    library.render_actions([action(ActionType.MOVE, "重命名动作"), wait])
    assert library.selected_action().name == "重命名动作"
    for category in (ActionType.MOVE, ActionType.MANIPULATE):
        assert library.action_tree.category_items[category].isExpanded()
    library.render_actions([wait])
    assert library.selected_action() is None
    assert library.current_category_type() is ActionType.MOVE
    assert not library.delete_button.isEnabled()


def test_reveal_waits_for_refresh_and_collapse_all_stays_collapsed(library: ActionLibraryView) -> None:
    trajectory = action(ActionType.TRAJECTORY)
    library.reveal_action(trajectory.id)
    library.render_actions([trajectory])
    assert library.selected_action() == trajectory
    assert library.action_tree.category_items[ActionType.TRAJECTORY].isExpanded()
    library.collapse_button.click()
    library.render_actions([trajectory])
    assert all(not item.isExpanded() for item in library.action_tree.category_items.values())
    assert library.current_category_type() is ActionType.TRAJECTORY


def test_double_click_inserts_and_only_actions_can_drag(library: ActionLibraryView) -> None:
    move = action(ActionType.MOVE)
    library.render_actions([move])
    library.reveal_action(move.id)
    tree = library.action_tree
    inserted = []
    library.action_insert_requested.connect(inserted.append)
    QApplication.processEvents()
    position = tree.visualItemRect(tree.currentItem()).center()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert inserted == [move]
    library.set_canvas_scale_provider(lambda: 0.75)
    with patch("src.gui.views.action_tree.start_action_drag") as drag:
        tree.startDrag(Qt.DropAction.CopyAction)
        drag.assert_called_once_with(tree, move, 0.75)
        tree.select_category(ActionType.MOVE)
        tree.startDrag(Qt.DropAction.CopyAction)
        assert drag.call_count == 1


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_compact_layout_and_theme_keep_categories_and_toolbar_visible(library: ActionLibraryView, mode: ThemeMode) -> None:
    application = QApplication.instance()
    palette, stylesheet = application.palette(), application.styleSheet()
    try:
        ThemeController(application, mode)
        library.resize(280, 480)
        library.render_actions([action(ActionType.MOVE, "很长的动作名称" * 12)])
        library.reveal_action(ActionType.MOVE.value)
        application.processEvents()
        assert library.width() <= 280
        assert library.header.geometry().bottom() < library.action_tree.geometry().top()
        assert library.collapse_button.geometry().right() < library.header.width()
        assert not library.action_tree.horizontalScrollBar().isVisible()
        assert not library.action_tree.currentItem().icon(0).isNull()
    finally:
        application.setPalette(palette)
        application.setStyleSheet(stylesheet)
