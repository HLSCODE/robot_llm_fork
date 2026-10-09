from __future__ import annotations

import os
import asyncio
import io
from pathlib import Path
import sys
from unittest.mock import patch

from scripts.build_windows_portable import copy_release_templates, shorten_license_paths
from src.bootstrap import portable
from src.bootstrap.initialization import InitializationPlan, InitializationRunner, InitializationStep
from src.bootstrap.initialization_cli import _build_parser
from src.configuration.config_initializer import initialize_configuration
from src.configuration.runtime_paths import application_root


def test_portable_root_is_executable_directory_not_resource_directory(tmp_path: Path) -> None:
    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "executable", str(tmp_path / "robot-llm.exe")),
    ):
        assert application_root() == tmp_path.resolve()


def test_portable_environment_uses_installation_and_respects_cache_override(tmp_path: Path) -> None:
    original_cwd = Path.cwd()
    try:
        with (
            patch.object(portable, "application_root", return_value=tmp_path),
            patch.object(portable, "is_portable", return_value=True),
            patch.dict(os.environ, {"HF_HOME": "custom-cache"}, clear=True),
        ):
            portable.prepare_environment()
            assert Path.cwd() == tmp_path
            assert os.environ["HF_HOME"] == "custom-cache"
            assert os.environ["MODELSCOPE_CACHE"] == str(tmp_path / "models/modelscope")
    finally:
        os.chdir(original_cwd)


def test_release_templates_exclude_private_files_and_initialize_incrementally(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "release"
    examples = {
        ".env.example": "TOKEN=",
        "config/config.example.toml": 'include = ["fragments/robots/tianji.toml"]',
        "config/fragments/robots/tianji.example.toml": '[robot_providers.tianji]\nip = ""',
        "docs/windows-portable.md": "Documentation",
        "models/kws/keywords.txt": "keyword",
    }
    private_files = {
        ".env": "SECRET=value",
        "config/config.toml": "private",
        "config/fragments/robots/tianji.toml": "private-device",
        "data/profiles/user/actions/library.json": "private-data",
        "models/weights.pt": "weights",
        "logs/secret.log": "private-log",
    }
    for name, content in (examples | private_files).items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    target.mkdir()
    copy_release_templates(source, target)
    for name in private_files:
        assert not (target / name).exists()
    initialize_configuration(target)
    (target / ".env").write_text("LOCAL=keep", encoding="utf-8")
    initialize_configuration(target)
    assert (target / ".env").read_text(encoding="utf-8") == "LOCAL=keep"
    assert (target / "config/fragments/robots/tianji.toml").is_file()


def test_portable_dependency_step_never_calls_uv(tmp_path: Path) -> None:
    runner = InitializationRunner()
    plan = InitializationPlan(tmp_path, (InitializationStep.DEPENDENCIES,))
    with (
        patch("src.bootstrap.initialization.is_portable", return_value=True),
        patch.object(runner, "_run_subprocess") as run,
    ):
        runner._sync_dependencies(plan, InitializationStep.DEPENDENCIES)
        run.assert_not_called()


def test_portable_asr_uses_worker_not_python_script(tmp_path: Path) -> None:
    runner = InitializationRunner()
    plan = InitializationPlan(tmp_path, (InitializationStep.ASR_MODELS,))
    with (
        patch("src.bootstrap.initialization.is_portable", return_value=True),
        patch.object(runner, "_run_subprocess") as run,
    ):
        runner._prepare_asr_models(plan, InitializationStep.ASR_MODELS)
    command = run.call_args.args[0]
    assert command == [sys.executable, "--prepare-asr-worker", "--project-root", str(tmp_path)]


def test_portable_defaults_exclude_dependency_sync() -> None:
    with patch("src.bootstrap.initialization_cli.is_portable", return_value=True):
        args = _build_parser().parse_args([])
    assert "dependencies" not in args.steps.split(",")


def test_portable_redirected_output_is_utf8() -> None:
    buffer = io.BytesIO()
    output = io.TextIOWrapper(buffer, encoding="ascii")
    with (
        patch.object(sys, "stdout", output),
        patch.object(sys, "stderr", output),
        patch.object(sys, "argv", ["robot-init.exe", "--portable-smoke-check"]),
        patch.object(portable, "prepare_environment"),
        patch.object(portable, "smoke_check", side_effect=lambda: print("初始化完成") or 0),
    ):
        assert portable.initializer_main() == 0
    assert buffer.getvalue().decode("utf-8").strip() == "初始化完成"


def test_long_wheel_licenses_keep_distinct_contents_and_original_names() -> None:
    names = [f"torch.dist-info/licenses/{'third_party/' * 15}{vendor}/LICENSE"
             for vendor in ("one", "two")]
    entries = [(name, f"source-{index}", "DATA") for index, name in enumerate(names)]
    entries.append(("torch/lib/torch.dll", "native-library", "BINARY"))
    shortened, manifest = shorten_license_paths(entries)
    assert len({name for name, _, _ in shortened}) == 3
    assert all(len(name) < 100 for name, _, _ in shortened)
    assert set(manifest.values()) == set(names)
    assert [source for _, source, _ in shortened] == [source for _, source, _ in entries]
    assert shortened[-1] == entries[-1]


def test_portable_wizard_select_all_does_not_offer_dependencies(tmp_path: Path) -> None:
    from src.bootstrap.initialization_tui import ChoiceList, InitializationApp

    async def exercise() -> None:
        plan = InitializationPlan(tmp_path, (InitializationStep.CONFIGURATION,), dry_run=True)
        app = InitializationApp(plan)
        with patch("src.bootstrap.initialization_tui.is_portable", return_value=True):
            async with app.run_test() as pilot:
                await pilot.press("ctrl+a")
                selected = app.query_one("#steps-list", ChoiceList).selected_ids
                assert "configuration" in selected
                assert "dependencies" not in selected

    asyncio.run(exercise())
