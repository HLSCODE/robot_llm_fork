from __future__ import annotations

from pathlib import Path
from dataclasses import fields
import shutil
from tempfile import TemporaryDirectory
import tomllib
import unittest

from src.configuration.config_initializer import initialize_configuration, main
from src.configuration.settings import ServerSettings
from src.configuration.toml_source import load_toml_sections


class ConfigInitializerTests(unittest.TestCase):
    def test_repository_templates_have_one_complete_server_configuration(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        templates = repository / "config" / "fragments"
        owners = []
        for template in templates.rglob("*.example.toml"):
            with template.open("rb") as stream:
                if "server" in tomllib.load(stream):
                    owners.append(template.relative_to(templates).as_posix())
        self.assertEqual(["server.example.toml"], owners)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config" / "fragments").mkdir(parents=True)
            shutil.copyfile(repository / ".env.example", root / ".env.example")
            shutil.copyfile(repository / "config" / "config.example.toml", root / "config" / "config.example.toml")
            for template in templates.rglob("*.example.toml"):
                target = root / "config" / "fragments" / template.relative_to(templates)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(template, target)
            result = initialize_configuration(root)
            server_file = root / "config" / "fragments" / "server.toml"
            self.assertIn(server_file, result.created)
            sections = load_toml_sections(root / "config" / "config.toml")
            # Legacy security flag is intentionally replaced by the common flag
            # in new examples; every other server setting has a single home.
            self.assertEqual(
                {field.name for field in fields(ServerSettings)} - {"websocket_security_enabled"},
                set(sections["server"]),
            )
            self.assertTrue(sections["server"]["security_enabled"])
            self.assertFalse(sections["server"]["tcp_enabled"])
            content = server_file.read_bytes() + b"\n# local customization\n"
            server_file.write_bytes(content)
            rerun = initialize_configuration(root)
            self.assertIn(server_file, rerun.skipped)
            self.assertEqual(content, server_file.read_bytes())

    def test_initialization_copies_config_fragments_and_env(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_templates(root)

            result = initialize_configuration(root)

            self.assertEqual(5, len(result.created))
            self.assertFalse(result.skipped)
            self.assertEqual("secret-template\n", (root / ".env").read_text(encoding="utf-8"))
            self.assertEqual(
                "entry-template\n",
                (root / "config" / "config.toml").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "app-template\n",
                (root / "config" / "fragments" / "application.toml").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(
                "robot-template\n",
                (root / "config" / "fragments" / "robots" / "realman.toml").read_text(
                    encoding="utf-8"
                ),
            )

    def test_existing_files_are_skipped_without_modification(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_templates(root)
            initialize_configuration(root)
            local_config = root / "config" / "config.toml"
            local_config.write_text("user-value\n", encoding="utf-8")

            result = initialize_configuration(root)

            self.assertFalse(result.created)
            self.assertEqual(5, len(result.skipped))
            self.assertEqual("user-value\n", local_config.read_text(encoding="utf-8"))

    def test_missing_required_template_returns_error(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)

            with self.assertRaisesRegex(FileNotFoundError, "缺少配置模板"):
                initialize_configuration(root)
            self.assertEqual(1, main(["--project-root", str(root)]))

    @staticmethod
    def _write_templates(root: Path) -> None:
        fragment_directory = root / "config" / "fragments"
        fragment_directory.mkdir(parents=True)
        (root / ".env.example").write_text("secret-template\n", encoding="utf-8")
        (root / "config" / "config.example.toml").write_text(
            "entry-template\n", encoding="utf-8"
        )
        (fragment_directory / "application.example.toml").write_text(
            "app-template\n", encoding="utf-8"
        )
        (fragment_directory / "devices.example.toml").write_text(
            "device-template\n", encoding="utf-8"
        )
        robot_directory = fragment_directory / "robots"
        robot_directory.mkdir()
        (robot_directory / "realman.example.toml").write_text(
            "robot-template\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
