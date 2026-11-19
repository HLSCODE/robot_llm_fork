from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from src.domain.action_schema import ARM_SPEED_PERCENT
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.workflow import WorkflowDocument
from src.gui.theme import DARK_COLORS, LIGHT_COLORS, build_palette, build_stylesheet
from src.gui.views import WorkflowCanvasWidget
from src.gui.views.dialogs import ActionConfigDialog, OptionalIntegerEditor


@pytest.mark.parametrize("colors", [LIGHT_COLORS, DARK_COLORS])
def test_arm_speed_form_inherits_overrides_and_restores(colors):
    app = QApplication.instance() or QApplication([])
    dialog = ActionConfigDialog(
        ActionType.MOVE,
        {"name": "移至观察点", "parameters": {"目标": "机械臂", "模式": "move_l", "点位": [0] * 6}},
        robot_provider="tianji",
    )
    dialog.setPalette(build_palette(colors))
    dialog.setStyleSheet(build_stylesheet(colors))
    dialog.show()
    try:
        form = dialog.action_form
        editor = form.findChild(OptionalIntegerEditor)
        assert editor.inherit.isChecked()
        assert editor.spin_box.isHidden()
        assert ARM_SPEED_PERCENT not in form.parameters()
        editor.inherit.setChecked(False)
        editor.spin_box.setValue(6)
        app.processEvents()
        assert editor.spin_box.isVisible()
        assert editor.rect().contains(editor.spin_box.geometry())
        assert form.parameters()[ARM_SPEED_PERCENT] == 6
        mode = next(combo for combo in form.findChildren(QComboBox) if combo.findData("move_joints") >= 0)
        mode.setCurrentIndex(mode.findData("move_joints"))
        app.processEvents()
        editor = next(editor for editor in form.findChildren(OptionalIntegerEditor) if editor.isVisible())
        assert editor.value() == 6
        editor.inherit.setChecked(True)
        app.processEvents()
        assert ARM_SPEED_PERCENT not in form.parameters()
    finally:
        dialog.close()


def test_node_speed_edit_is_independent_and_roundtrips_in_saved_workflow():
    app = QApplication.instance() or QApplication([])
    base = ActionDefinition("move", "移动", ActionType.MOVE, {
        "目标": "机械臂", "模式": "move_l", "点位": [0] * 6, ARM_SPEED_PERCENT: 9,
    })
    canvas = WorkflowCanvasWidget()
    canvas.render_entries([SequenceItem.from_definition(base), SequenceItem.from_definition(base)])
    canvas.set_current_entry_row(0)
    edited = replace(base, parameters={**base.parameters, ARM_SPEED_PERCENT: 4})
    assert canvas.update_current_action(edited)
    document = canvas.document(workflow_id="speed-test", name="speed-test", revision=0)
    entries = WorkflowDocument.from_dict(document.to_dict()).to_entries()
    assert [entry.definition.parameters[ARM_SPEED_PERCENT] for entry in entries] == [4, 9]
    assert base.parameters[ARM_SPEED_PERCENT] == 9
    canvas.close()
    app.processEvents()
