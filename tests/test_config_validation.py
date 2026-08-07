from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.configuration.config_loader import ConfigLoadError, load_application_settings
from src.configuration.config_validation import (
    ConfigurationSeverity,
    StartupOptions,
    redact_config_mapping,
    validate_startup_configuration,
)
from src.configuration.data_paths import ApplicationDataPaths
from src.bootstrap.launcher import main
from src.configuration.settings import ApplicationSettings


def _config(root: Path, **overrides):
    values = {
        "ROBOT_DATA_DIR": str(root),
        "ACTIONS_LIBRARY_DIRECTORY": "",
        "WORKFLOWS_DIRECTORY": "",
        "WORKFLOW_DRAFTS_DIRECTORY": "",
        "SKILL_LIBRARY_DIRECTORY": "",
        "LOG_LEVEL": "INFO",
        "LLM_DEFAULT_PROVIDER": "minicpm",
        "OPENAI_API_KEY": "",
        "WEBSOCKET_SECURITY_ENABLED": True,
        "WEBSOCKET_AUTH_TOKEN": "",
        "WEBSOCKET_CONTROL_LEASE_SECONDS": 30.0,
        "WEBSOCKET_SEND_TIMEOUT_SECONDS": 2.0,
        "TELEOPERATION_COMMAND_TIMEOUT_SECONDS": 1.0,
        "WEBSOCKET_MAX_MESSAGE_SIZE_BYTES": 1_048_576,
        "WEBSOCKET_MAX_REQUESTS_PER_SECOND": 120,
        "WEBSOCKET_MAX_CONCURRENT_REQUESTS": 16,
        "WEBSOCKET_MAX_QUEUED_MESSAGES": 16,
        "AUXILIARY_SERVICE_START_TIMEOUT_SECONDS": 5.0,
        "AUXILIARY_SERVICE_STOP_TIMEOUT_SECONDS": 10.0,
        "EXECUTION_ACTION_TIMEOUT_SECONDS": 600.0,
        "SAFETY_STOP_WAIT_TIMEOUT_SECONDS": 2.0,
        "EXECUTION_ARM_MOVE_RETRY_DELAY_SECONDS": 0.5,
        "EXECUTION_GRIPPER_RETRY_DELAY_SECONDS": 0.5,
    }
    values.update(overrides)
    return ApplicationSettings.from_config(SimpleNamespace(**values))


def _options(**overrides) -> StartupOptions:
    values = {
        "simulation": True,
        "websocket_enabled": True,
        "websocket_host": "127.0.0.1",
        "websocket_port": 8765,
        "log_level": "INFO",
    }
    values.update(overrides)
    return StartupOptions(**values)


class ConfigurationValidationTests(unittest.TestCase):
    def test_valid_simulation_configuration_passes(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(Path(temporary_directory)),
                _options(),
            )

        self.assertEqual((), report.errors)
        self.assertEqual((), report.warnings)

    def test_invalid_active_values_are_reported_together(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    WEBSOCKET_AUTH_TOKEN="change-me",
                    EXECUTION_ACTION_TIMEOUT_SECONDS=0,
                ),
                _options(websocket_port=70_000, log_level="verbose"),
            )

        self.assertEqual(
            {
                "invalid_log_level",
                "invalid_port",
                "invalid_number",
                "placeholder_secret",
            },
            {issue.code for issue in report.errors},
        )
        self.assertTrue(
            all(issue.severity is ConfigurationSeverity.ERROR for issue in report.errors)
        )

    def test_invalid_logging_storage_values_are_reported(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    LOG_DIRECTORY=" ",
                    LOG_RETENTION_DAYS=0,
                ),
                _options(),
            )

        self.assertEqual(
            {"invalid_log_directory", "invalid_number"},
            {issue.code for issue in report.errors},
        )

    def test_unknown_gui_theme_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(Path(temporary_directory), GUI_THEME="midnight"),
                _options(),
            )

        self.assertEqual("invalid_gui_theme", report.errors[0].code)
        self.assertEqual("GUI_THEME", report.errors[0].field)

    def test_non_positive_voice_startup_wait_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S=0,
                ),
                _options(),
            )

        self.assertEqual(
            {"invalid_number"},
            {issue.code for issue in report.errors},
        )
        self.assertEqual(
            "VOICE_SPEECH_STARTUP_WAIT_TIMEOUT_S",
            report.errors[0].field,
        )

    def test_invalid_vision_versions_and_artifact_retention_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    VISION_SCHEMA_VERSION=0,
                    VISION_MODEL_VERSION=" ",
                    VISION_CALIBRATION_VERSION="",
                    VISION_DEBUG_RETENTION_DAYS=0,
                    VISION_DEBUG_MAX_RUNS=0,
                ),
                _options(),
            )

        self.assertEqual(
            {"invalid_number", "invalid_vision_version"},
            {issue.code for issue in report.errors},
        )

    def test_invalid_external_localization_settings_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    EXTERNAL_LOCALIZATION_HOST=" ",
                    EXTERNAL_LOCALIZATION_PORT=70_000,
                    EXTERNAL_LOCALIZATION_RECEIVE_SIZE_BYTES=0,
                ),
                _options(simulation=False),
            )

        fields = {issue.field for issue in report.errors}
        self.assertIn("EXTERNAL_LOCALIZATION_HOST", fields)
        self.assertIn("EXTERNAL_LOCALIZATION_PORT", fields)
        self.assertIn("EXTERNAL_LOCALIZATION_RECEIVE_SIZE_BYTES", fields)

    def test_non_loopback_binding_without_tls_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    WEBSOCKET_AUTH_TOKEN="real-test-token",
                ),
                _options(websocket_host="0.0.0.0"),
            )

        self.assertEqual(
            {
                "websocket_origins_required",
                "websocket_tls_required",
            },
            {issue.code for issue in report.errors},
        )
        self.assertEqual((), report.warnings)

    def test_remote_binding_is_allowed_when_websocket_security_is_disabled(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    WEBSOCKET_SECURITY_ENABLED=False,
                ),
                _options(websocket_host="0.0.0.0"),
            )

        self.assertEqual((), report.errors)
        self.assertEqual(
            {"websocket_security_disabled"},
            {issue.code for issue in report.warnings},
        )

    def test_loopback_reverse_proxy_requires_auth_and_origins(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            valid = validate_startup_configuration(
                _config(
                    root,
                    WEBSOCKET_AUTH_TOKEN="real-test-token",
                    WEBSOCKET_REVERSE_PROXY_MODE=True,
                    WEBSOCKET_ALLOWED_ORIGINS=("https://robot.example",),
                ),
                _options(),
            )
            invalid = validate_startup_configuration(
                _config(
                    root,
                    WEBSOCKET_REVERSE_PROXY_MODE=True,
                    WEBSOCKET_ALLOWED_ORIGINS=("https://robot.example/path",),
                ),
                _options(),
            )

        self.assertEqual((), valid.errors)
        self.assertEqual(
            {
                "invalid_websocket_origin",
                "websocket_auth_required",
            },
            {issue.code for issue in invalid.errors},
        )

    def test_tls_certificate_and_key_must_be_configured_together(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    WEBSOCKET_TLS_CERTIFICATE_PATH="missing.crt",
                ),
                _options(),
            )

        self.assertEqual(
            {
                "incomplete_websocket_tls",
                "missing_websocket_tls_file",
            },
            {issue.code for issue in report.errors},
        )

    def test_slow_send_threshold_must_not_exceed_send_timeout(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    WEBSOCKET_SEND_TIMEOUT_SECONDS=1.0,
                    WEBSOCKET_SLOW_SEND_THRESHOLD_SECONDS=1.1,
                ),
                _options(),
            )

        self.assertIn(
            "invalid_websocket_slow_send_threshold",
            {issue.code for issue in report.errors},
        )

    def test_data_collection_and_balance_settings_are_validated_at_startup(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            report = validate_startup_configuration(
                _config(
                    Path(temporary_directory),
                    DATA_COLLECTION_FPS=0,
                    DATA_COLLECTION_ARMS=("left", "left"),
                    BALANCE_CAMERA_WAIT_TIMEOUT_SECONDS=0,
                ),
                _options(websocket_enabled=False),
            )

        self.assertEqual(
            {
                "BALANCE_CAMERA_WAIT_TIMEOUT_SECONDS",
                "DATA_COLLECTION_ARMS",
                "DATA_COLLECTION_FPS",
            },
            {issue.field for issue in report.errors},
        )

    def test_data_path_collision_is_rejected_before_file_creation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = validate_startup_configuration(
                _config(
                    root,
                    ACTIONS_LIBRARY_DIRECTORY="same",
                    SKILL_LIBRARY_DIRECTORY="same",
                ),
                _options(websocket_enabled=False),
            )

        self.assertIn(
            "colliding_data_paths",
            {issue.code for issue in report.errors},
        )

    def test_sensitive_mapping_values_are_always_redacted(self) -> None:
        redacted = redact_config_mapping(
            {
                "OPENAI_API_KEY": "very-secret",
                "WEBSOCKET_AUTH_TOKEN": "token",
                "LOG_LEVEL": "INFO",
            }
        )

        self.assertEqual("<redacted>", redacted["OPENAI_API_KEY"])
        self.assertEqual("<redacted>", redacted["WEBSOCKET_AUTH_TOKEN"])
        self.assertEqual("INFO", redacted["LOG_LEVEL"])
        self.assertNotIn("very-secret", repr(redacted))

    def test_config_parse_error_does_not_echo_rejected_value(self) -> None:
        with patch(
            "src.configuration.config_loader._EnvironmentConfig._load_unchecked",
            side_effect=ValueError("very-secret-invalid-value"),
        ):
            with self.assertRaises(ConfigLoadError) as error:
                load_application_settings()

        self.assertNotIn("very-secret-invalid-value", str(error.exception))

    def test_removed_single_camera_calibration_variables_are_ignored(self) -> None:
        legacy_environment = {
            "VISION_RELOCALIZATION_CAMERA_MATRIX": "1,0,0,0,1,0,0,0,1",
            "VISION_RELOCALIZATION_CAMERA_MATRIX_RESOLUTION": "1,1",
            "VISION_RELOCALIZATION_DIST_COEFFS": "1,1,1,1,1",
            "VISION_RELOCALIZATION_MARKER_WIDTH": "9.9",
            "VISION_RELOCALIZATION_MARKER_HEIGHT": "9.9",
        }
        with (
            patch.dict("os.environ", legacy_environment, clear=True),
            patch("src.configuration.config_loader.load_dotenv"),
        ):
            settings = load_application_settings().vision

        self.assertNotEqual(
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            settings.vision_relocalization_left_camera_matrix,
        )
        self.assertEqual(0.158, settings.vision_relocalization_default_marker_width)
        self.assertEqual(0.158, settings.vision_relocalization_default_marker_height)

    def test_data_paths_follow_root_and_explicit_overrides(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = ApplicationDataPaths.from_settings(
                _config(
                    root,
                    WORKFLOWS_DIRECTORY="custom/workflows",
                    WORKFLOW_DRAFTS_DIRECTORY="custom/drafts",
                ).data
            )

        self.assertEqual(root.resolve(), paths.root)
        self.assertEqual(root.resolve() / "actions", paths.actions_directory)
        self.assertEqual(root.resolve() / "skills", paths.skills_directory)
        self.assertEqual(
            Path.cwd().resolve() / "custom" / "workflows",
            paths.workflows_directory,
        )
        self.assertEqual(
            Path.cwd().resolve() / "custom" / "drafts",
            paths.workflow_drafts_directory,
        )

    def test_check_config_command_does_not_start_gui(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            config = _config(
                Path(temporary_directory),
                SIMULATION_MODE=True,
                WEBSOCKET_ENABLED=False,
            )
            with (
                patch("sys.argv", ["robot-llm", "--check-config"]),
                patch(
                    "src.bootstrap.launcher.load_application_settings",
                    return_value=config,
                ),
                patch("src.bootstrap.launcher.configure_logging"),
                patch("src.bootstrap.launcher.run_gui") as run_gui,
            ):
                exit_code = main()

        self.assertEqual(0, exit_code)
        run_gui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
