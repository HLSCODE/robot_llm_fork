from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.configuration.config_loader import ConfigLoadError, load_application_settings


class ConfigurationLoadingTests(unittest.TestCase):
    def test_missing_conventional_file_uses_typed_defaults(self) -> None:
        with (
            TemporaryDirectory() as temporary_directory,
            patch.dict(os.environ, {}, clear=True),
            patch("src.configuration.config_loader.default_config_path") as default_path,
        ):
            default_path.return_value = Path(temporary_directory) / "missing.toml"
            settings = load_application_settings(env_file=Path(temporary_directory) / ".env")

        self.assertEqual("system", settings.gui.theme)
        self.assertEqual(8765, settings.server.websocket_port)

    def test_explicit_missing_file_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing.toml"
            with self.assertRaisesRegex(ConfigLoadError, "配置文件不存在"):
                load_application_settings(missing)

    def test_toml_values_are_loaded_and_environment_has_priority(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 2
                [gui]
                theme = "light"
                [server]
                websocket_port = 9000
                websocket_allowed_origins = ["https://robot.example"]
                [robot]
                robot1_initial_pose = [1, 2, 3, 4, 5, 6]
                """,
            )
            with patch.dict(
                os.environ,
                {"GUI_THEME": "dark", "WEBSOCKET_PORT": "9100"},
                clear=True,
            ):
                settings = load_application_settings(
                    config_path,
                    env_file=root / "missing.env",
                )

        self.assertEqual("dark", settings.gui.theme)
        self.assertEqual(9100, settings.server.websocket_port)
        self.assertEqual(("https://robot.example",), settings.server.websocket_allowed_origins)
        self.assertEqual((1.0, 2.0, 3.0, 4.0, 5.0, 6.0), settings.robot.robot1_initial_pose)

    def test_dotenv_is_loaded_without_overriding_process_environment(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(root, "schema_version = 2\n")
            env_path = root / ".env"
            env_path.write_text(
                'OPENAI_API_KEY="file-secret"\nGUI_THEME="light"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GUI_THEME": "dark"}, clear=True):
                settings = load_application_settings(config_path, env_file=env_path)

        self.assertEqual("dark", settings.gui.theme)
        self.assertEqual("file-secret", settings.secrets.openai_api_key)

    def test_unknown_table_and_field_are_rejected(self) -> None:
        documents = (
            "schema_version = 2\n[unknown]\nvalue = 1\n",
            "schema_version = 2\n[gui]\ntheme = \"dark\"\ntypo = true\n",
        )
        for document in documents:
            with self.subTest(document=document), TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                config_path = self._write(root, document)
                with self.assertRaisesRegex(ConfigLoadError, "未知"):
                    load_application_settings(config_path, env_file=root / "missing.env")

    def test_wake_welcome_uses_workflow_field_without_legacy_alias(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 2
                [voice]
                voice_wake_welcome_enabled = true
                voice_wake_welcome_workflow = "welcome.workflow.json"
                """,
            )
            settings = load_application_settings(
                config_path,
                env_file=root / "missing.env",
            )

        self.assertTrue(settings.voice.voice_wake_welcome_enabled)
        self.assertEqual(
            "welcome.workflow.json",
            settings.voice.voice_wake_welcome_workflow,
        )
        self.assertEqual(
            "welcome.workflow.json",
            settings.voice.as_runtime_mapping()["wake_welcome_workflow"],
        )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy_config_path = self._write(
                root,
                """
                schema_version = 2
                [voice]
                voice_wake_welcome_task = "welcome.task"
                """,
            )
            with self.assertRaisesRegex(ConfigLoadError, "未知"):
                load_application_settings(
                    legacy_config_path,
                    env_file=root / "missing.env",
                )

    def test_secrets_are_rejected_in_toml(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                'schema_version = 2\n[secrets]\nopenai_api_key = "do-not-store"\n',
            )
            with self.assertRaisesRegex(ConfigLoadError, "敏感字段不得写入 TOML"):
                load_application_settings(config_path, env_file=root / "missing.env")

    def test_schema_version_and_field_types_are_strict(self) -> None:
        documents = (
            "[gui]\ntheme = \"dark\"\n",
            "schema_version = 1\n",
            'schema_version = 2\n[server]\nwebsocket_port = "8765"\n',
        )
        for document in documents:
            with self.subTest(document=document), TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                config_path = self._write(root, document)
                with self.assertRaises(ConfigLoadError):
                    load_application_settings(config_path, env_file=root / "missing.env")

    def test_invalid_environment_value_names_field_without_echoing_secret(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(root, "schema_version = 2\n")
            with (
                patch.dict(
                    os.environ,
                    {"WEBSOCKET_PORT": "very-secret-invalid-value"},
                    clear=True,
                ),
                self.assertRaises(ConfigLoadError) as error,
            ):
                load_application_settings(config_path, env_file=root / "missing.env")

        message = str(error.exception)
        self.assertIn("WEBSOCKET_PORT", message)
        self.assertNotIn("very-secret-invalid-value", message)

    def test_repository_example_is_a_complete_valid_document(self) -> None:
        example = Path(__file__).resolve().parents[1] / "config" / "config.example.toml"
        with patch.dict(os.environ, {}, clear=True):
            settings = load_application_settings(example, env_file=example.parent / "missing.env")

        self.assertEqual("realman", settings.robot.robot_provider)
        self.assertTrue(settings.server.websocket_enabled)

    @staticmethod
    def _write(root: Path, content: str) -> Path:
        path = root / "config.toml"
        path.write_text(content.strip() + "\n", encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
