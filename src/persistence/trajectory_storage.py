from __future__ import annotations

from pathlib import Path
import re
from shutil import copy2


_TRAJECTORY_FILE_PATTERN = re.compile(r"trajectory_(\d+)\.txt")


class TrajectoryStorage:
    """Allocate application-owned trajectory paths below one configured root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def directory_for(self, arm_key: str) -> Path:
        normalized = arm_key.strip().lower()
        if not normalized or Path(normalized).name != normalized:
            raise ValueError("trajectory arm key must be one path segment")
        directory = self._root / normalized
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def next_recording_path(self, arm_key: str) -> Path:
        directory = self.directory_for(arm_key)
        existing_numbers = [
            int(match.group(1))
            for path in directory.iterdir()
            if path.is_file()
            and (match := _TRAJECTORY_FILE_PATTERN.fullmatch(path.name)) is not None
        ]
        next_number = max(existing_numbers, default=0) + 1
        return directory / f"trajectory_{next_number:03d}.txt"

    def import_file(self, arm_key: str, source: Path) -> Path:
        resolved_source = source.resolve()
        if not resolved_source.is_file():
            raise FileNotFoundError(resolved_source)
        directory = self.directory_for(arm_key)
        if resolved_source.parent == directory:
            return resolved_source

        destination = self._available_import_path(directory, resolved_source.name)
        copy2(resolved_source, destination)
        return destination

    @staticmethod
    def _available_import_path(directory: Path, file_name: str) -> Path:
        source_name = Path(file_name)
        candidate = directory / source_name.name
        sequence = 1
        while candidate.exists():
            candidate = directory / (
                f"{source_name.stem}_{sequence:03d}{source_name.suffix}"
            )
            sequence += 1
        return candidate
