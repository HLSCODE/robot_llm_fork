from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.configuration.config_initializer import initialize_configuration, main


class ConfigInitializerTests(unittest.TestCase):
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
