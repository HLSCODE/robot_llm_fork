from dataclasses import replace
import json
import os
from unittest.mock import patch

import pytest

from src.configuration.config_loader import load_application_settings
from src.configuration.end_effectors import EndEffectorMount, EndEffectorSettings, EndEffectorTool
from src.configuration.errors import ConfigLoadError
from src.configuration.settings import ApplicationSettings
from src.devices.end_effectors import end_effectors
from src.devices.runtime.arm_models import ArmId
from src.devices.runtime.factory import create_device_runtime
from src.devices.runtime.models import DeviceContractError, DeviceState
from tool_support import tool_settings


def test_builtin_tool_ids_match_arm_neutral_configuration_examples():
    from pathlib import Path
    import tomllib

    root = Path(__file__).resolve().parents[1]
    tool_ids = set()
    for path in (root / "config/fragments/end-effectors").glob("*.example.toml"):
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        tool_ids.update(document["end_effectors"].get("tools", {}))
    assert tool_ids == {"robot_gripper_1", "zhixing_gripper_1", "pipette_1", "tool_changer_1", "dh_rgd_gripper_1"}

    def check_references(value):
        if isinstance(value, dict):
            if "tool_id" in value:
                assert value["tool_id"] in tool_ids
            for child in value.values():
                check_references(child)
        elif isinstance(value, list):
            for child in value:
                check_references(child)

    for path in (root / "src/builtin_catalogs").rglob("*.json"):
        check_references(json.loads(path.read_text(encoding="utf-8")))


def test_catalogs_merge_by_identity_with_entry_instance_override(tmp_path):
    fragments = {
        "native.toml": '[end_effectors.tools.native]\nkind="gripper"\nprovider="robot"\n',
        "external.toml": '[end_effectors.connections.bus]\nport="COM90"\n[end_effectors.tools.gripper]\nkind="gripper"\nprovider="zhixing"\nconnection="bus"\nstation=7\n',
        "mounts.toml": '[end_effectors.mounts.left]\nmode="none"\n[end_effectors.mounts.right]\nmode="fixed"\ntool="gripper"\n',
    }
    for name, content in fragments.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    entry = tmp_path / "config.toml"
    entry.write_text(
        "schema_version=6\ninclude="
        + json.dumps(list(fragments))
        + '\n[end_effectors.tools.gripper]\nkind="gripper"\nprovider="zhixing"\nconnection="bus"\nstation=11\n',
        encoding="utf-8",
    )
    with patch.dict(os.environ, {}, clear=True):
        settings = load_application_settings(entry, env_file=tmp_path / "missing.env")
    assert {t.id for t in settings.end_effectors.tools} == {"native", "gripper"}
    assert next(t for t in settings.end_effectors.tools if t.id == "gripper").station == 11
    runtime = create_device_runtime(settings, simulation=True)
    try:
        assert "tool:native" not in runtime.registered_device_ids()
        assert end_effectors(runtime).choices("gripper", "left") == []
        assert end_effectors(runtime).resolve("gripper", arm="right").id == "gripper"
    finally:
        runtime.shutdown_all()


def test_unmounted_device_catalog_never_constructs_or_connects_tools():
    settings = replace(
        tool_settings(),
        end_effectors=replace(
            tool_settings().end_effectors,
            mounts=(EndEffectorMount("left", "none"), EndEffectorMount("right", "none")),
        ),
    )
    with patch(
        "src.devices.end_effectors.factory._create_tool",
        side_effect=AssertionError("unselected device created"),
    ):
        runtime = create_device_runtime(settings, simulation=True)
    try:
        assert not any(identity.startswith("tool:") for identity in runtime.registered_device_ids())
    finally:
        runtime.shutdown_all()


def test_native_robot_gripper_uses_mount_arm_without_sdk_arm_setting():
    settings = tool_settings()
    settings = replace(
        settings,
        end_effectors=replace(
            settings.end_effectors,
            mounts=(
                EndEffectorMount("left", "none"),
                EndEffectorMount("right", "fixed", tool="left"),
            ),
        ),
    )
    runtime = create_device_runtime(settings, simulation=True)
    try:
        routing = end_effectors(runtime)
        target = routing.resolve("gripper", arm="right")
        robot = runtime.require("robot-system")
        with patch.object(robot, "open_gripper") as opened:
            routing.gripper(target).open()
        opened.assert_called_once_with(ArmId.RIGHT)
    finally:
        runtime.shutdown_all()


def test_unsupported_native_gripper_does_not_initialize_or_stop_robot_startup():
    defaults = ApplicationSettings.defaults()
    settings = replace(
        defaults,
        robot=replace(defaults.robot, provider="tianji"),
        end_effectors=EndEffectorSettings(
            tools=(EndEffectorTool("native", "gripper", "robot"),),
            mounts=(EndEffectorMount("left", "fixed", tool="native"),),
        ),
    )
    runtime = create_device_runtime(settings, simulation=True)
    try:
        assert runtime.snapshot("robot-system").state is DeviceState.REGISTERED
        assert runtime.snapshot("tool:native").state is DeviceState.FAILED
        routing = end_effectors(runtime)
        assert routing.choices("gripper", "left") == []
        with pytest.raises(DeviceContractError, match="内置夹爪"):
            routing.resolve("gripper", arm="left")
        assert runtime.initialize("robot-system") is not None
    finally:
        runtime.shutdown_all()


def test_optional_tool_factory_failure_is_isolated_and_recorded():
    defaults = ApplicationSettings.defaults()
    from src.configuration.settings import CameraProfile

    settings = replace(
        tool_settings(),
        vision=replace(
            defaults.vision, cameras=(CameraProfile(name="test", provider="opencv", device_id="0"),)
        ),
    )
    with (
        patch(
            "src.devices.end_effectors.factory.ADP",
            side_effect=RuntimeError("protocol dependency unavailable"),
        ),
        patch(
            "src.devices.transports.core.transport._load_serial_factory",
            side_effect=AssertionError("port opened during startup"),
        ),
    ):
        runtime = create_device_runtime(settings, simulation=False)
    try:
        assert runtime.snapshot("tool:pipette").state is DeviceState.FAILED
        assert runtime.snapshot("tool:changer").state is DeviceState.READY
        assert runtime.snapshot("robot-system").state is DeviceState.REGISTERED
    finally:
        runtime.shutdown_all()


def test_none_mount_rejects_hidden_tool_and_old_sdk_fields_are_not_accepted(tmp_path):
    with pytest.raises(ConfigLoadError, match="未安装末端"):
        EndEffectorSettings(mounts=(EndEffectorMount("left", "none", tool="hidden"),))
    entry = tmp_path / "old.toml"
    entry.write_text(
        'schema_version=6\n[end_effectors.tools.native]\nkind="gripper"\nprovider="robot"\nsdk_arm="left"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigLoadError, match="sdk_arm"):
        load_application_settings(entry, env_file=tmp_path / "missing.env")
