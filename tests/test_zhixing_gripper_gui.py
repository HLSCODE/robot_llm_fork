from PySide6.QtWidgets import QApplication

from src.domain.models import ActionType
from src.gui.views.dialogs import SchemaActionForm


def test_vendor_specific_action_is_no_longer_exposed():
    application = QApplication.instance() or QApplication([])
    form = SchemaActionForm(ActionType.MANIPULATE, initial_variant="夹爪")
    assert "编号" not in form.field_names
    assert "arm" in form.field_names
    assert "tool_id" in form.field_names
    assert "闭合度" in form.field_names
    form.close()
    form.deleteLater()
    application.processEvents()
