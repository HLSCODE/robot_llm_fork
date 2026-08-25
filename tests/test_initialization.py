from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.bootstrap.initialization import (
    EventKind,
    InitializationEvent,
    InitializationPlan,
    InitializationRunner,
    InitializationStep,
    StepStatus,
    _resolve_cached_funasr_model,
    prepare_asr_models,
)
from src.bootstrap.initialization_cli import _run_textual, main
from src.configuration.settings import VoiceSettings


class InitializationPlanTests(unittest.TestCase):
    def test_selected_steps_are_normalized_to_dependency_order(self) -> None:
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(
                InitializationStep.VALIDATION,
                InitializationStep.KWS_MODEL,
                InitializationStep.CONFIGURATION,
            ),
        )

        self.assertEqual(
            (
                InitializationStep.CONFIGURATION,
                InitializationStep.KWS_MODEL,
                InitializationStep.VALIDATION,
            ),
            plan.ordered_steps,
        )

    def test_unknown_extra_is_rejected_at_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知依赖组"):
            InitializationPlan(
                project_root=Path.cwd(),
                steps=(InitializationStep.DEPENDENCIES,),
                extras=("unknown",),
            )


class InitializationRunnerTests(unittest.TestCase):
    def test_dry_run_publishes_step_events(self) -> None:
        events: list[InitializationEvent] = []
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION, InitializationStep.VALIDATION),
            dry_run=True,
        )

        results = InitializationRunner(events.append).run(plan)

        self.assertTrue(all(result.status is StepStatus.SUCCEEDED for result in results))
        self.assertEqual(
            [
                StepStatus.RUNNING,
                StepStatus.SUCCEEDED,
                StepStatus.RUNNING,
                StepStatus.SUCCEEDED,
            ],
            [event.status for event in events if event.kind is EventKind.STATUS],
        )
        self.assertEqual(2, len([event for event in events if event.kind is EventKind.LOG]))

    def test_failure_skips_following_steps(self) -> None:
        events: list[InitializationEvent] = []
        runner = InitializationRunner(events.append)
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION, InitializationStep.VALIDATION),
        )

        def fail(plan: InitializationPlan, step: InitializationStep) -> None:
            raise RuntimeError("boom")

        with patch.object(runner, "_step_handler", return_value=fail):
            results = runner.run(plan)

        self.assertEqual(StepStatus.FAILED, results[0].status)
        self.assertEqual(StepStatus.SKIPPED, results[1].status)

    def test_dependency_sync_uses_copy_mode_across_filesystems(self) -> None:
        runner = InitializationRunner()
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.DEPENDENCIES,),
            extras=("gui",),
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "src.bootstrap.initialization._uv_cache_directory",
                return_value=Path("cache"),
            ),
            patch(
                "src.bootstrap.initialization._are_on_different_filesystems",
                return_value=True,
            ),
            patch.object(runner, "_run_subprocess") as run_subprocess,
        ):
            runner._sync_dependencies(plan, InitializationStep.DEPENDENCIES)

        command = run_subprocess.call_args.args[0]
        self.assertIn("--link-mode=copy", command)

    def test_configuration_step_preserves_existing_local_values(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fragments = root / "config" / "fragments"
            fragments.mkdir(parents=True)
            (root / ".env.example").write_text("TOKEN=example\n", encoding="utf-8")
            (root / "config" / "config.example.toml").write_text(
                'include = ["fragments/app.toml"]\n', encoding="utf-8"
            )
            (fragments / "app.example.toml").write_text("[gui]\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=user\n", encoding="utf-8")
            plan = InitializationPlan(
                project_root=root,
                steps=(InitializationStep.CONFIGURATION,),
            )

            results = InitializationRunner().run(plan)

            self.assertEqual(StepStatus.SUCCEEDED, results[0].status)
            self.assertEqual("TOKEN=user\n", (root / ".env").read_text(encoding="utf-8"))

    def test_cached_asr_bundle_is_not_downloaded_again(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            vad = self._create_cached_model(root / "vad")
            asr = self._create_cached_model(root / "asr")
            punc = self._create_cached_model(root / "punc")
            settings = VoiceSettings(
                voice_vad_model=str(vad),
                voice_asr_model=str(asr),
                voice_asr_punc_model=str(punc),
            )
            messages: list[str] = []

            with (
                patch("src.voice_interaction.speech.vad.FunASRVAD") as vad_factory,
                patch(
                    "src.voice_interaction.speech.asr.FunASRRecognizer"
                ) as asr_factory,
            ):
                prepare_asr_models(settings, log=messages.append)

            vad_factory.assert_not_called()
            asr_factory.assert_not_called()
            self.assertEqual(3, sum("跳过下载" in message for message in messages))

    def test_modelscope_alias_resolves_legacy_local_cache_without_network(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            expected = self._create_cached_model(
                cache_root
                / "models"
                / "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch"
                / "snapshots"
                / "master"
            )
            with patch.dict("os.environ", {"MODELSCOPE_CACHE": str(cache_root)}):
                resolved = _resolve_cached_funasr_model("fsmn-vad")

            self.assertEqual(expected.resolve(), resolved)

    @staticmethod
    def _create_cached_model(path: Path) -> Path:
        path.mkdir(parents=True)
        (path / "config.yaml").write_text("model: Test\n", encoding="utf-8")
        (path / "model.pt").write_bytes(b"model")
        return path


class InitializationCliTests(unittest.TestCase):
    def test_non_interactive_dry_run_does_not_require_textual(self) -> None:
        exit_code = main(
            [
                "--non-interactive",
                "--dry-run",
                "--steps",
                "configuration,validation",
                "--extras",
                "",
            ]
        )

        self.assertEqual(0, exit_code)

    def test_textual_initializer_uses_full_screen_mode(self) -> None:
        plan = InitializationPlan(
            project_root=Path.cwd(),
            steps=(InitializationStep.CONFIGURATION,),
            dry_run=True,
        )
        with patch("src.bootstrap.initialization_tui.InitializationApp") as app_type:
            app_type.return_value.run.return_value = 0

            exit_code = _run_textual(plan)

        self.assertEqual(0, exit_code)
        app_type.return_value.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
