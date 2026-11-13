from dataclasses import replace
from unittest.mock import patch

import pytest

from src.application import create_application_services
from src.application.composition import CompositionRevisionConflict
from src.configuration.settings import ApplicationSettings
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.workflow import WorkflowDocument


@pytest.fixture
def services(tmp_path):
    defaults = ApplicationSettings.defaults()
    services = create_application_services(
        replace(defaults, data=replace(defaults.data, robot_data_dir=str(tmp_path / "data"))),
        simulation=True,
    )
    document = WorkflowDocument.from_entries(
        workflow_id="stable-id", name="original", revision=0,
        robot_profile_id=services.composition.robot_profile_id,
        entries=[SequenceItem.from_definition(ActionDefinition(
            "wait", "等待", ActionType.WAIT, {"wait_seconds": 1},
            robot_profile_id=services.composition.robot_profile_id,
        ))],
    )
    services.composition.save_workflow("original", document, origin="test")
    return services


def update(services, *, name="renamed", allow_remote=True, document=None):
    document = document or services.composition.load_workflow("original")
    return services.workflow_editing.update_task_properties(
        "original", name, allow_remote=allow_remote,
        expected_revision=document.revision, expected_workflow_id=document.workflow_id,
    )


def test_properties_rename_preserves_content_identity_and_updates_discovery(services):
    original = services.composition.load_workflow("original")
    assert update(services) == "renamed.workflow.json"
    saved = services.composition.load_workflow("renamed")
    assert saved.workflow_id == original.workflow_id
    assert saved.root == original.root and saved.positions == original.positions
    assert saved.name == "renamed" and saved.revision == original.revision + 1
    assert saved.allow_remote
    assert services.saved_tasks.list_tasks() == ("renamed.workflow.json",)
    with pytest.raises(FileNotFoundError):
        services.composition.load_workflow("original")


def test_properties_of_open_task_preserve_unsaved_canvas(services):
    session = services.workflow_editing
    state = session.open("original")
    session.replace_document(replace(state.document, root=replace(state.document.root, children=())))
    update(services)
    state = session.snapshot()
    assert state.workflow_name == "renamed.workflow.json" and state.dirty
    assert not state.document.root.children
    assert state.document.allow_remote and state.document.name == "renamed"
    assert services.composition.load_workflow("renamed").root.children


def test_property_conflict_and_duplicate_name_do_not_change_files(services):
    original = services.composition.load_workflow("original")
    services.composition.save_workflow("other", original, origin="test")
    with pytest.raises(FileExistsError):
        update(services, name="other")
    assert services.composition.load_workflow("original") == original
    assert services.composition.load_workflow("other").allow_remote is False
    update(services, name="original", allow_remote=False)
    with pytest.raises(CompositionRevisionConflict):
        update(services, document=original)


@pytest.mark.parametrize("name", ["", "   ", "../outside", "dir/task", "dir\\task"])
def test_invalid_property_names_leave_original_unchanged(services, name):
    original = services.composition.load_workflow("original")
    with pytest.raises(ValueError):
        update(services, name=name)
    assert services.composition.load_workflow("original") == original


def test_property_write_failure_preserves_original(services):
    original = services.composition.load_workflow("original")
    with patch("src.persistence.storage.write_json_atomic", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            update(services)
    assert services.composition.load_workflow("original") == original
    assert services.composition.list_workflows() == ("original.workflow.json",)
