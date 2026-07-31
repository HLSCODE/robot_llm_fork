from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.core.config_loader import ConfigLoadError, load_application_settings
from src.core.config_validation import (
    ConfigurationSeverity,
    StartupOptions,
    redact_config_mapping,
    validate_startup_configuration,
)
from src.core.data_paths import ApplicationDataPaths
from src.core.launcher import main
from src.core.settings import ApplicationSettings


def _config(root: Path, **overrides):
    values = {
        "ROBOT_DATA_DIR": str(root),
        "ACTIONS_LIBRARY_PATH": "",
        "TASKS_DIRECTORY": "",
        "SKILL_LIBRARY_PATH": "",
        "LOG_LEVEL": "INFO",
        "LLM_DEFAULT_PROVIDER": "minicpm",
        "OPENAI_API_KEY": "",
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
                    BALANCE_CAMERA_INDEX=-1,
                    VVEAI_API_KEY="change-me",
                ),
                _options(websocket_enabled=False),
            )

        self.assertEqual(
            {
                "BALANCE_CAMERA_INDEX",
                "DATA_COLLECTION_ARMS",
                "DATA_COLLECTION_FPS",
                "VVEAI_API_KEY",
            },
            {issue.field for issue in report.errors},
        )

    def test_data_path_collision_is_rejected_before_file_creation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            report = validate_startup_configuration(
                _config(
                    root,
                    ACTIONS_LIBRARY_PATH="same.json",
                    SKILL_LIBRARY_PATH="same.json",
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
            "src.core.config_loader._EnvironmentConfig._load_unchecked",
            side_effect=ValueError("very-secret-invalid-value"),
        ):
            with self.assertRaises(ConfigLoadError) as error:
                load_application_settings()

        self.assertNotIn("very-secret-invalid-value", str(error.exception))

    def test_data_paths_follow_root_and_explicit_overrides(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = ApplicationDataPaths.from_settings(
                _config(root, TASKS_DIRECTORY="custom/tasks").data
            )

        self.assertEqual(root.resolve(), paths.root)
        self.assertEqual(root.resolve() / "actions_library.json", paths.actions_file)
        self.assertEqual(root.resolve() / "skills" / "skill_library.json", paths.skills_file)
        self.assertEqual(
            Path.cwd().resolve() / "custom" / "tasks",
            paths.tasks_directory,
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
                    "src.core.launcher.load_application_settings",
                    return_value=config,
                ),
                patch("src.core.launcher.setup_logging"),
                patch("src.core.launcher.run_gui") as run_gui,
            ):
                exit_code = main()

        self.assertEqual(0, exit_code)
        run_gui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
