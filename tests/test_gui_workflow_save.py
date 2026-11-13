from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.gui.theme import DARK_COLORS, LIGHT_COLORS, build_palette, build_stylesheet
from src.gui.views.dialogs import WorkflowSaveDialog


@pytest.mark.parametrize("colors", [LIGHT_COLORS, DARK_COLORS])
@pytest.mark.parametrize("edit_properties", [False, True])
def test_workflow_save_permission_is_explicit_and_theme_compatible(colors, edit_properties):
    app = QApplication.instance() or QApplication([])
    dialog = WorkflowSaveDialog("移液实验", edit_properties=edit_properties)
    assert dialog.windowTitle() == ("编辑任务属性" if edit_properties else "另存为流程")
    dialog.setPalette(build_palette(colors))
    dialog.setStyleSheet(build_stylesheet(colors))
    dialog.show()
    app.processEvents()
    assert dialog.name_edit.text() == "移液实验"
    assert not dialog.allow_remote_checkbox.isChecked()
    assert dialog.allow_remote_checkbox.geometry().height() > 0
    assert dialog.rect().contains(dialog.allow_remote_checkbox.mapTo(dialog, dialog.allow_remote_checkbox.rect().bottomRight()))
    dialog.close()
    restored = WorkflowSaveDialog("已开放任务", allow_remote=True)
    assert restored.allow_remote_checkbox.isChecked()
    restored.close()
