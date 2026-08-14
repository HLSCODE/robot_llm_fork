from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.persistence.trajectory_storage import TrajectoryStorage


class TrajectoryStorageTests(unittest.TestCase):
    def test_allocates_sequential_paths_below_configured_arm_directory(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "trajectories"
            storage = TrajectoryStorage(root)

            first = storage.next_recording_path("left")
            first.write_text("trajectory", encoding="utf-8")
            second = storage.next_recording_path("left")

        self.assertEqual(root.resolve() / "left" / "trajectory_001.txt", first)
        self.assertEqual(root.resolve() / "left" / "trajectory_002.txt", second)

    def test_rejects_arm_keys_that_escape_the_storage_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            storage = TrajectoryStorage(Path(temporary_directory))

            with self.assertRaisesRegex(ValueError, "one path segment"):
                storage.directory_for("../outside")

    def test_imports_external_files_without_overwriting_existing_data(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "demo.txt"
            source.write_text("first", encoding="utf-8")
            storage = TrajectoryStorage(root / "trajectories")

            first = storage.import_file("right", source)
            second = storage.import_file("right", source)

            self.assertEqual("demo.txt", first.name)
            self.assertEqual("demo_001.txt", second.name)
            self.assertEqual("first", first.read_text(encoding="utf-8"))
            self.assertEqual("first", second.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
