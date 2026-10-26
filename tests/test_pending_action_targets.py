import pytest
from dataclasses import replace
from PySide6.QtWidgets import QApplication

from src.domain.action_schema import PENDING_TARGET_FIELDS, validate_action_parameters
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.workflow import WorkflowDocument
from src.application.workflow_compiler import WorkflowCompiler, WorkflowCompilationError
from src.configuration.end_effectors import EndEffectorSettings
from src.devices.runtime.runtime import DeviceRuntime
from src.devices.end_effectors.runtime import EndEffectorRuntime
from src.gui.views.action_tree import ActionCategoryTree
from src.gui.views.dialogs import SchemaActionForm
from src.persistence.storage import JsonCompositionRepository
from src.configuration.settings import ApplicationSettings
from src.configuration.data_paths import ApplicationDataPaths
from src.application import create_application_services


def parameters():
    return {
        "执行器": "吸液枪",
        "操作": "吐",
        "容量": 10,
        "arm": "",
        "tool_id": "",
        PENDING_TARGET_FIELDS: ["arm", "tool_id"],
    }


def test_pending_target_valid_only_for_loading_not_execution():
    params = parameters()
    assert validate_action_parameters(
        ActionType.MANIPULATE, params, allow_pending_targets=True
    ).is_valid
    strict = validate_action_parameters(ActionType.MANIPULATE, params)
    assert not strict.is_valid and "待配置" in strict.message
    params["容量"] = "bad"
    assert not validate_action_parameters(
        ActionType.MANIPULATE, params, allow_pending_targets=True
    ).is_valid


@pytest.mark.parametrize("marker", [[], ["other"], ["arm", "arm"], "arm", [None], [{}]])
def test_invalid_marker_never_bypasses_validation(marker):
    params = parameters()
    params[PENDING_TARGET_FIELDS] = marker
    assert not validate_action_parameters(
        ActionType.MANIPULATE, params, allow_pending_targets=True
    ).is_valid


def test_pending_parameters_cannot_be_used_as_other_action_type():
    assert not validate_action_parameters(
        ActionType.WAIT, parameters(), allow_pending_targets=True
    ).is_valid


def test_pending_library_and_workflow_load_but_compile_and_direct_route_fail(tmp_path):
    action = ActionDefinition("a", "吐液", ActionType.MANIPULATE, parameters())
    repository = JsonCompositionRepository(
        actions_directory=tmp_path / "actions", workflows_directory=tmp_path / "workflows"
    )
    repository.save_actions([action])
    assert repository.load_actions()[0].parameters == action.parameters
    document = WorkflowDocument.from_entries(
        workflow_id="w", name="w", revision=1, entries=[SequenceItem("node", action)]
    )
    repository.save_workflow("w", document)
    loaded = repository.load_workflow("w")
    assert loaded is not None
    with pytest.raises(WorkflowCompilationError, match="待配置"):
        WorkflowCompiler().compile(loaded)
    routing = EndEffectorRuntime(DeviceRuntime(), EndEffectorSettings())
    with pytest.raises(ValueError, match="待配置"):
        routing.from_parameters("pipette", action.parameters)


def test_application_starts_with_pending_action_and_no_tools(tmp_path):
    defaults = ApplicationSettings.defaults()
    settings = replace(defaults, data=replace(defaults.data, robot_data_dir=str(tmp_path)))
    paths = ApplicationDataPaths.from_settings(settings.data, settings.robot_profile_id())
    action = ActionDefinition(
        "pending", "吐液", ActionType.MANIPULATE, parameters(), settings.robot_profile_id()
    )
    JsonCompositionRepository(
        actions_directory=paths.actions_directory, robot_profile_id=settings.robot_profile_id()
    ).save_actions([action])
    services = create_application_services(settings, simulation=True)
    try:
        stored = services.composition.list_actions()
        assert stored[0].parameters[PENDING_TARGET_FIELDS] == ["arm", "tool_id"]
        assert not services.manual_control.end_effectors.routing.settings.tools
    finally:
        services.devices.shutdown_all()


def test_gui_requires_explicit_arm_and_tool_and_removes_marker_on_save():
    app = QApplication.instance() or QApplication([])
    form = SchemaActionForm(
        ActionType.MANIPULATE,
        parameters(),
        tool_choices_reader=lambda kind, arm: [("pipette_1", "吸液枪")],
    )
    tree = ActionCategoryTree()
    try:
        tree.render_actions([ActionDefinition("a", "吐液", ActionType.MANIPULATE, parameters())])
        assert "待配置" in tree._action_items["a"].text(0)
        arm = form._field_widgets["arm"]
        tool = form._field_widgets["tool_id"]
        assert arm.currentData() == ""
        assert tool.currentData() == ""
        assert set(form.mark_missing_required_fields()) >= {"arm", "tool_id"}
        arm.setCurrentIndex(arm.findData("right"))
        assert "tool_id" in form.mark_missing_required_fields()
        tool.setCurrentIndex(tool.findData("pipette_1"))
        repaired = form.parameters()
        assert repaired["arm"] == "right" and repaired["tool_id"] == "pipette_1"
        assert PENDING_TARGET_FIELDS not in repaired
        assert validate_action_parameters(ActionType.MANIPULATE, repaired).is_valid
        tree.render_actions([ActionDefinition("a", "吐液", ActionType.MANIPULATE, repaired)])
        assert "待配置" not in tree._action_items["a"].text(0)
    finally:
        form.close()
        tree.close()
        form.deleteLater()
        tree.deleteLater()
        app.processEvents()
