from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.configuration.config_loader import (
    ConfigLoadError,
    configuration_source_paths,
    default_config_path,
    load_application_settings,
)
from src.configuration.settings import CameraRole


class ConfigurationLoadingTests(unittest.TestCase):
    def test_default_config_path_prefers_working_directory_then_project_root(self) -> None:
        with TemporaryDirectory() as working_directory, TemporaryDirectory() as project_directory:
            working_root = Path(working_directory)
            project_root = Path(project_directory)
            project_config = project_root / "config" / "config.toml"
            project_config.parent.mkdir()
            project_config.write_text("schema_version = 5\n", encoding="utf-8")

            self.assertEqual(
                project_config,
                default_config_path(
                    working_directory=working_root,
                    project_root=project_root,
                ),
            )

            working_config = working_root / "config" / "config.toml"
            working_config.parent.mkdir()
            working_config.write_text("schema_version = 5\n", encoding="utf-8")
            self.assertEqual(
                working_config,
                default_config_path(
                    working_directory=working_root,
                    project_root=project_root,
                ),
            )

    def test_default_env_follows_project_config_when_started_elsewhere(self) -> None:
        with TemporaryDirectory() as temporary_directory, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary_directory)
            config_path = root / "config" / "config.toml"
            config_path.parent.mkdir()
            config_path.write_text(
                'schema_version = 5\n[gui]\ntheme = "light"\n',
                encoding="utf-8",
            )
            (root / ".env").write_text('GUI_THEME="dark"\n', encoding="utf-8")
            with patch(
                "src.configuration.config_loader.default_config_path",
                return_value=config_path,
            ):
                settings = load_application_settings()

        self.assertEqual("dark", settings.gui.theme)

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
                schema_version = 5
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

    def test_includes_are_ordered_entry_overrides_fragments_and_env_wins(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fragments = root / "fragments"
            fragments.mkdir()
            self._write_named(
                fragments / "base.toml",
                """
                [gui]
                theme = "dark"
                [server]
                websocket_port = 9000
                websocket_allowed_origins = ["https://base.example"]
                """,
            )
            self._write_named(
                fragments / "override.toml",
                """
                [gui]
                theme = "system"
                [server]
                websocket_port = 9100
                websocket_allowed_origins = ["https://override.example"]
                """,
            )
            config_path = self._write(
                root,
                """
                schema_version = 5
                include = [
                  "fragments/base.toml",
                  "fragments/override.toml",
                ]
                [gui]
                theme = "light"
                """,
            )
            with patch.dict(os.environ, {"WEBSOCKET_PORT": "9200"}, clear=True):
                settings = load_application_settings(
                    config_path,
                    env_file=root / "missing.env",
                )
            sources = configuration_source_paths(config_path)

        self.assertEqual("light", settings.gui.theme)
        self.assertEqual(9200, settings.server.websocket_port)
        self.assertEqual(
            ("https://override.example",),
            settings.server.websocket_allowed_origins,
        )
        self.assertEqual(
            (
                config_path.resolve(),
                (fragments / "base.toml").resolve(),
                (fragments / "override.toml").resolve(),
            ),
            sources,
        )

    def test_sequence_fields_are_replaced_instead_of_appended(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fragments = root / "fragments"
            fragments.mkdir()
            self._write_named(
                fragments / "cameras.toml",
                """
                [vision]
                [[vision.cameras]]
                name = "included"
                provider = "realsense"
                device_id = "included-serial"
                roles = []
                arms = []
                """,
            )
            config_path = self._write(
                root,
                """
                schema_version = 5
                include = ["fragments/cameras.toml"]
                [vision]
                [[vision.cameras]]
                name = "entry"
                provider = "realsense"
                device_id = "entry-serial"
                roles = []
                arms = []
                """,
            )
            settings = load_application_settings(
                config_path,
                env_file=root / "missing.env",
            )

        self.assertEqual(("entry",), tuple(camera.name for camera in settings.vision.cameras))

    def test_include_paths_are_confined_unique_and_must_exist(self) -> None:
        cases = (
            ('include = ["missing.toml"]', "配置文件不存在"),
            ('include = ["../outside.toml"]', "不允许超出"),
            ('include = ["fragment.toml", "fragment.toml"]', "include 重复"),
        )
        for include_declaration, expected_error in cases:
            with self.subTest(include=include_declaration), TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                self._write_named(root / "fragment.toml", "[gui]\ntheme = \"dark\"")
                config_path = self._write(
                    root,
                    f"schema_version = 5\n{include_declaration}",
                )
                with self.assertRaisesRegex(ConfigLoadError, expected_error):
                    load_application_settings(
                        config_path,
                        env_file=root / "missing.env",
                    )

    def test_fragment_cannot_declare_metadata_and_errors_name_the_source(self) -> None:
        documents = (
            ("schema_version = 5", "不得声明"),
            ('include = ["nested.toml"]', "不得声明"),
            ("[gui]\ntypo = true", "fragment.toml"),
        )
        for fragment_document, expected_error in documents:
            with self.subTest(fragment=fragment_document), TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                self._write_named(root / "fragment.toml", fragment_document)
                config_path = self._write(
                    root,
                    'schema_version = 5\ninclude = ["fragment.toml"]',
                )
                with self.assertRaisesRegex(ConfigLoadError, expected_error):
                    load_application_settings(
                        config_path,
                        env_file=root / "missing.env",
                    )

    def test_dotenv_is_loaded_without_overriding_process_environment(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(root, "schema_version = 5\n")
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
            "schema_version = 5\n[unknown]\nvalue = 1\n",
            "schema_version = 5\n[gui]\ntheme = \"dark\"\ntypo = true\n",
        )
        for document in documents:
            with self.subTest(document=document), TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                config_path = self._write(root, document)
                with self.assertRaisesRegex(ConfigLoadError, "未知"):
                    load_application_settings(config_path, env_file=root / "missing.env")

    def test_camera_catalog_loads_nested_typed_profiles(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 5
                [vision]
                vision_default_workflow = "bottle"

                [[vision.cameras]]
                name = "monitor1"
                label = "左臂视觉相机"
                provider = "realsense"
                device_id = "serial-left"
                roles = ["vision_capture", "robot_grasp", "relocalization"]
                arms = ["left"]
                capture_rotation_matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1]
                capture_translation_vector = [0.1, 0.2, 0.3]
                capture_gripper_offset = [3.14, 0, 1.57]
                camera_matrix = [1, 0, 2, 0, 3, 4, 0, 0, 1]
                camera_matrix_resolution = [1920, 1080]
                distortion_coefficients = [0.1, 0.2, 0, 0, 0.3]
                end_effector_to_camera = [
                    [1, 0, 0, 0],
                    [0, 1, 0, 0],
                    [0, 0, 1, 0],
                    [0, 0, 0, 1],
                ]
                """,
            )
            settings = load_application_settings(
                config_path,
                env_file=root / "missing.env",
            ).vision

        self.assertEqual((("monitor1", "左臂视觉相机"),), settings.camera_choices())
        self.assertEqual(
            "monitor1",
            settings.camera_name_for_role(CameraRole.RELOCALIZATION, arm="left"),
        )
        self.assertEqual("serial-left", settings.cameras[0].device_id)
        self.assertEqual(
            (0.1, 0.2, 0.3),
            settings.cameras[0].capture_translation_vector,
        )
        self.assertEqual((1920.0, 1080.0), settings.cameras[0].camera_matrix_resolution)

    def test_legacy_camera_identity_fields_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 5
                [vision]
                camera_provider = "realsense"
                realsense_device_sn = "serial-left"
                vision_relocalization_left_camera_name = "monitor1"
                """,
            )
            with self.assertRaisesRegex(ConfigLoadError, "未知"):
                load_application_settings(
                    config_path,
                    env_file=root / "missing.env",
                )

    def test_wake_welcome_uses_workflow_field_without_legacy_alias(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 5
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
                schema_version = 5
                [voice]
                voice_wake_welcome_task = "welcome.task"
                """,
            )
            with self.assertRaisesRegex(ConfigLoadError, "未知"):
                load_application_settings(
                    legacy_config_path,
                    env_file=root / "missing.env",
                )

    def test_legacy_minicpm_ask_configuration_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 5
                [llm]
                minicpm_ask_enabled = true
                """,
            )
            with self.assertRaisesRegex(ConfigLoadError, "未知"):
                load_application_settings(
                    config_path,
                    env_file=root / "missing.env",
                )

    def test_llm_provider_instances_are_loaded_from_named_subtables(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 5
                [llm]
                default_provider = "laboratory_qwen"

                [llm_providers.laboratory_qwen]
                kind = "openai_compatible"
                model = "qwen-custom"
                base_url = "http://127.0.0.1:9000/v1"
                credential_env = "OPENAI_API_KEY"
                output_modes = ["text"]
                """,
            )
            settings = load_application_settings(
                config_path,
                env_file=root / "missing.env",
            )

        provider = settings.llm_providers.require("laboratory_qwen")
        self.assertEqual("openai_compatible", provider.normalized_kind)
        self.assertEqual("qwen-custom", provider.model)
        self.assertEqual(("laboratory_qwen",), settings.llm_providers.enabled_ids)

    def test_legacy_provider_fields_in_llm_table_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                """
                schema_version = 5
                [llm]
                llm_default_provider = "openai"
                openai_model = "gpt-4o"
                """,
            )
            with self.assertRaisesRegex(ConfigLoadError, "未知"):
                load_application_settings(
                    config_path,
                    env_file=root / "missing.env",
                )

    def test_secrets_are_rejected_in_toml(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._write(
                root,
                'schema_version = 5\n[secrets]\nopenai_api_key = "do-not-store"\n',
            )
            with self.assertRaisesRegex(ConfigLoadError, "敏感字段不得写入 TOML"):
                load_application_settings(config_path, env_file=root / "missing.env")

    def test_schema_version_and_field_types_are_strict(self) -> None:
        documents = (
            "[gui]\ntheme = \"dark\"\n",
            "schema_version = 1\n",
            "schema_version = 2\n",
            "schema_version = 3\n",
            "schema_version = 4\n",
            'schema_version = 5\n[server]\nwebsocket_port = "8765"\n',
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
            config_path = self._write(root, "schema_version = 5\n")
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

    @staticmethod
    def _write_named(path: Path, content: str) -> None:
        path.write_text(content.strip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
