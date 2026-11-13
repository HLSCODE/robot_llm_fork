from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase

from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.outputs import NodeOutput
from src.gui.theme import DARK_COLORS, LIGHT_COLORS, build_palette, build_stylesheet
from src.gui.views.dialogs import ActionConfigDialog
from src.gui.views.node_output import NodeOutputEditor
from src.gui.views import WorkflowCanvasWidget


@pytest.fixture
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_output_editor_is_opt_in_and_roundtrips(application):
    editor = NodeOutputEditor()
    assert editor.value() is None and editor.details.isHidden()
    editor.enabled.setChecked(True)
    editor.name.setText("tube")
    editor.field.setEditText("/height")
    editor.mode.setCurrentIndex(editor.mode.findData("all"))
    editor.required.setChecked(False)
    expected = NodeOutput("tube", "/height", "all", False)
    assert editor.value() == expected
    restored = NodeOutputEditor(expected)
    assert restored.value() == expected
    restored.enabled.setChecked(False)
    assert restored.value() is None


@pytest.mark.parametrize(
    "action_type,allow,shown",
    [
        (ActionType.WAIT, True, False),
        (ActionType.MOVE, True, False),
        (ActionType.VISION_RELOCALIZE, False, False),
        (ActionType.VISION_RELOCALIZE, True, True),
    ],
)
def test_only_output_capable_workflow_nodes_show_export_controls(
    application, action_type, allow, shown
):
    dialog = ActionConfigDialog(action_type, allow_node_output=allow)
    assert (dialog.output_editor is not None) is shown
    dialog.close()


def test_node_export_is_separate_from_action_definition(application):
    dialog = ActionConfigDialog(
        ActionType.VISION_RELOCALIZE,
        {
            "name": "检测",
            "parameters": {"action_mode": "teach", "station_name": "test", "arm": "left"},
        },
        allow_node_output=True,
    )
    dialog.output_editor.enabled.setChecked(True)
    dialog.output_editor.name.setText("tube")
    dialog.output_editor.field.setEditText("/height")
    dialog._validate_and_accept()
    assert dialog.result()
    assert dialog.get_node_output() == NodeOutput("tube", "/height")
    assert "output" not in dialog.get_action_definition().to_dict()
    dialog.close()


def test_canvas_output_edit_undo_and_redo(application):
    definition = ActionDefinition(
        "vision",
        "检测",
        ActionType.VISION_RELOCALIZE,
        {
            "action_mode": "teach",
            "arm": "left",
            "station_name": "test",
        },
    )
    canvas = WorkflowCanvasWidget()
    canvas.render_entries((SequenceItem("node", definition),))
    canvas.set_current_entry_row(0)
    binding = NodeOutput("tube", "/height")
    assert canvas.update_current_action(definition, output=binding, update_output=True)
    assert canvas.current_scope_entries()[0].output == binding
    canvas.undo()
    assert canvas.current_scope_entries()[0].output is None
    canvas.redo()
    assert canvas.current_scope_entries()[0].output == binding
    assert (
        canvas.document(workflow_id="test", name="test", revision=0).to_entries()[0].output
        == binding
    )
    canvas.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_output_form_has_no_overlaps_in_both_themes(application, theme):
    original = application.palette()
    original_style = application.styleSheet()
    original_font = application.font()
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
        application.setFont(QFont("Microsoft YaHei", 10))
    colors = LIGHT_COLORS if theme == "light" else DARK_COLORS
    application.setPalette(build_palette(colors))
    application.setStyleSheet(build_stylesheet(colors))
    dialog = ActionConfigDialog(
        ActionType.VISION_RELOCALIZE,
        {
            "name": "检测液面",
            "parameters": {"action_mode": "teach", "station_name": "test", "arm": "left"},
        },
        allow_node_output=True,
        node_output=NodeOutput("tube_liquid_level", "/height", "all"),
    )
    try:
        dialog.show()
        application.processEvents()
        dialog._adjust_to_content()
        application.processEvents()
        editor = dialog.output_editor
        assert editor.width() >= 300
        previous = -1
        for widget in (editor.name, editor.field, editor.mode, editor.required):
            geometry = widget.geometry()
            assert geometry.top() > previous
            assert geometry.width() >= widget.minimumSizeHint().width()
            previous = geometry.bottom()
        directory = Path("tmp/workflow-output-preview")
        directory.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(directory / f"node-output-{theme}.png"))
    finally:
        dialog.close()
        application.setPalette(original)
        application.setStyleSheet(original_style)
        application.setFont(original_font)
