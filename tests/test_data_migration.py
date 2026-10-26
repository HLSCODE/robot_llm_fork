from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from src.application.data_migration import DataMigrator, TargetMappings, upgrade_versions
from src.configuration.data_paths import ApplicationDataPaths
from src.configuration.settings import ApplicationSettings
from src.domain.models import ActionDefinition, ActionType, SequenceItem
from src.domain.workflow import WorkflowDocument
from src.persistence.storage import JsonCompositionRepository
from src.persistence.json_documents import write_json_atomic
from src.bootstrap.initialization_cli import main


def paths_for(root):
    return ApplicationDataPaths.from_settings(
        replace(ApplicationSettings.defaults().data, robot_data_dir="data"),
        "tianji-tianji-dual",
        project_root=root,
    )


def library(paths, version=3, *, missing_arm=True):
    action = ActionDefinition(
        "a",
        "吸液",
        ActionType.MANIPULATE,
        {
            "执行器": "吸液枪",
            "操作": "吸",
            "容量": 10,
            **({} if missing_arm else {"arm": "right", "tool_id": "offline-pipette"}),
        },
        paths.robot_profile_id,
    ).to_dict()
    return {
        "schema": "robot_llm.actions",
        "schema_version": version,
        "robot_profile_id": paths.robot_profile_id,
        "actions": [action],
    }


def save_library(paths, document):
    path = paths.actions_directory / "library.json"
    write_json_atomic(path, document)
    return path


def mappings():
    return TargetMappings(actions={"a": {"arm": "right", "tool_id": "offline-pipette"}})


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
def test_all_supported_action_versions_upgrade_once_to_latest(tmp_path, version):
    paths = paths_for(tmp_path)
    document = library(paths, version, missing_arm=version != 4)
    original = document["actions"] if version == 0 else document
    path = save_library(paths, original)
    before = path.read_bytes()
    plan = DataMigrator(paths, mappings()).plan()
    assert not plan.issues
    assert path.read_bytes() == before
    if version == 4:
        assert not plan.changes
        return
    assert plan.changes[0].versions == tuple(range(version, 5))
    backup = plan.apply(paths.root / "backups")
    assert backup is not None
    assert (backup / "0000.json").read_bytes() == before
    actions = JsonCompositionRepository(
        actions_directory=paths.actions_directory, robot_profile_id=paths.robot_profile_id
    ).load_actions()
    assert actions[0].parameters["arm"] == "right"
    assert actions[0].parameters["tool_id"] == "offline-pipette"
    assert not DataMigrator(paths).plan().changes


def test_missing_mapping_becomes_pending_without_guessing_devices(tmp_path):
    paths = paths_for(tmp_path)
    path = save_library(paths, library(paths))
    before = path.read_bytes()
    plan = DataMigrator(paths).plan()
    assert not plan.issues
    assert path.read_bytes() == before
    plan.apply(paths.root / "backups")
    parameters = json.loads(path.read_text(encoding="utf8"))["actions"][0]["parameters"]
    assert parameters["arm"] == parameters["tool_id"] == ""
    assert parameters["_pending_target_fields"] == ["arm", "tool_id"]
    assert not DataMigrator(paths).plan().changes


def test_all_files_preflight_before_any_publish(tmp_path):
    paths = paths_for(tmp_path)
    path = save_library(paths, library(paths, missing_arm=False))
    write_json_atomic(
        paths.skills_directory / "bad.skill.json",
        {"schema": "robot_llm.skill", "schema_version": 99},
    )
    plan = DataMigrator(paths).plan()
    assert plan.changes and plan.issues
    before = path.read_bytes()
    with pytest.raises(ValueError):
        plan.apply(paths.root / "backups")
    assert path.read_bytes() == before


def test_nested_workflow_and_skill_snapshots_are_migrated(tmp_path):
    paths = paths_for(tmp_path)
    action = ActionDefinition.from_dict(library(paths)["actions"][0])
    workflow = WorkflowDocument.from_entries(
        workflow_id="w",
        name="w",
        revision=1,
        entries=[SequenceItem("node", action)],
        robot_profile_id=paths.robot_profile_id,
    ).to_dict()
    workflow["schema_version"] = 5
    path = paths.workflows_directory / "w.workflow.json"
    write_json_atomic(path, workflow)
    skill_path = paths.skills_directory / "s.skill.json"
    write_json_atomic(
        skill_path,
        {
            "schema": "robot_llm.skill",
            "schema_version": 1,
            "skill": {
                "id": "s",
                "name": "s",
                "category": "检测",
                "description": "test",
                "parameters": [],
                "steps": [
                    {
                        "step_id": "1",
                        "action_name": "吸液",
                        "action_type": "MANIPULATE",
                        "parameters": action.parameters,
                    }
                ],
            },
        },
    )
    plan = DataMigrator(
        paths, TargetMappings(defaults={"吸液枪": {"arm": "right", "tool_id": "offline-pipette"}})
    ).plan()
    assert not plan.issues
    assert {change.versions for change in plan.changes} == {(5, 6), (1, 2, 3)}
    plan.apply(paths.root / "backups")
    restored = WorkflowDocument.from_dict(json.loads(path.read_text(encoding="utf8")))
    assert restored.to_entries()[0].definition.parameters["arm"] == "right"
    assert (
        json.loads(skill_path.read_text(encoding="utf8"))["skill"]["steps"][0]["parameters"][
            "tool_id"
        ]
        == "offline-pipette"
    )


@pytest.mark.parametrize("version", [-1, True, "3", 99])
def test_unknown_versions_are_not_guessed(tmp_path, version):
    paths = paths_for(tmp_path)
    save_library(paths, library(paths, version))
    assert DataMigrator(paths, mappings()).plan().issues


def test_missing_adjacent_converter_and_input_immutability():
    original = {"schema_version": 1, "parameters": {}}
    with pytest.raises(ValueError, match="缺少 v2"):
        upgrade_versions(original, 3, {1: lambda d: d["parameters"].update(changed=True)})
    assert original == {"schema_version": 1, "parameters": {}}


def test_write_failure_rolls_back_original_bytes(tmp_path):
    paths = paths_for(tmp_path)
    path = save_library(paths, library(paths, missing_arm=False))
    plan = DataMigrator(paths).plan()
    before = path.read_bytes()
    real_write = write_json_atomic

    def broken_write(target, document):
        if target == path:
            raise OSError("disk failure")
        real_write(target, document)

    with (
        patch("src.application.data_migration.write_json_atomic", side_effect=broken_write),
        pytest.raises(OSError),
    ):
        plan.apply(paths.root / "backups")
    assert path.read_bytes() == before


def test_concurrent_change_is_rejected_before_backup(tmp_path):
    paths = paths_for(tmp_path)
    path = save_library(paths, library(paths, missing_arm=False))
    plan = DataMigrator(paths).plan()
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="发生变化"):
        plan.apply(paths.root / "backups")


def test_legacy_gripper_number_requires_tool_mapping(tmp_path):
    paths = paths_for(tmp_path)
    document = library(paths)
    document["actions"][0]["parameters"] = {"执行器": "知行夹爪", "操作": "关", "编号": 1}
    save_library(paths, document)
    pending = DataMigrator(paths, TargetMappings(defaults={"夹爪": {"arm": "left"}})).plan()
    assert not pending.issues
    assert pending.changes[0].document["actions"][0]["parameters"]["_pending_target_fields"] == ["tool_id"]
    plan = DataMigrator(
        paths, TargetMappings(actions={"a": {"arm": "left", "tool_id": "zhixing_gripper_1"}})
    ).plan()
    assert not plan.issues
    parameters = plan.changes[0].document["actions"][0]["parameters"]
    assert parameters == {
        "执行器": "夹爪",
        "操作": "关",
        "arm": "left",
        "tool_id": "zhixing_gripper_1",
    }


def test_legacy_realman_trajectory_copy_is_missing_only(tmp_path):
    paths = ApplicationDataPaths.from_settings(
        replace(ApplicationSettings.defaults().data, robot_data_dir="data"),
        "realman-rm75-dual",
        project_root=tmp_path,
    )
    source = paths.root / "trajectories/left/a.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"trajectory")
    plan = DataMigrator(paths).plan()
    assert not plan.issues
    plan.apply(paths.root / "backups")
    target = paths.trajectories_directory / "left/a.txt"
    assert target.read_bytes() == b"trajectory"
    assert source.read_bytes() == b"trajectory"
    target.write_bytes(b"newer-recording")
    assert not DataMigrator(paths).plan().changes


def test_old_task_with_tool_parameters_is_preflighted_before_creation(tmp_path):
    paths = paths_for(tmp_path)
    source = paths.root / "tasks/old.task"
    action = ActionDefinition.from_dict(library(paths)["actions"][0])
    write_json_atomic(source, [SequenceItem("n", action).to_dict()])
    config = tmp_path / "config/config.toml"
    config.parent.mkdir()
    config.write_text('schema_version=6\n[robot]\nprovider="tianji"\n', encoding="utf8")
    assert main(["migrate-data", "--project-root", str(tmp_path)]) == 0
    restored = WorkflowDocument.from_dict(json.loads(
        (paths.workflows_directory / "old.workflow.json").read_text(encoding="utf8")
    ))
    assert restored.to_entries()[0].definition.parameters["_pending_target_fields"] == ["arm", "tool_id"]
    assert source.exists()


def test_cli_dry_run_lists_files_and_never_changes_data(tmp_path, capsys):
    config = tmp_path / "config/config.toml"
    config.parent.mkdir()
    config.write_text('schema_version=6\n[robot]\nprovider="tianji"\n', encoding="utf8")
    paths = paths_for(tmp_path)
    path = save_library(paths, library(paths))
    map_path = tmp_path / "targets.json"
    map_path.write_text(
        json.dumps({"actions": {"a": {"arm": "right", "tool_id": "offline-pipette"}}}),
        encoding="utf8",
    )
    before = path.read_bytes()
    assert (
        main(
            [
                "migrate-data",
                "--project-root",
                str(tmp_path),
                "--target-map",
                str(map_path),
                "--dry-run",
            ]
        )
        == 0
    )
    assert "v3 → v4" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert not (paths.root / "migration-backups").exists()
