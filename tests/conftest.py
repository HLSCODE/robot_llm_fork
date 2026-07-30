from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from src.core.data_paths import ApplicationDataPaths


@pytest.fixture(autouse=True)
def isolate_default_application_data(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep service-construction tests away from workstation user data."""
    original_resolver = ApplicationDataPaths.from_config
    with TemporaryDirectory(
        prefix=".pytest-application-data-",
        dir=Path.cwd(),
    ) as temporary_directory:
        root = Path(temporary_directory)

        def resolve(config: object) -> ApplicationDataPaths:
            if hasattr(config, "ROBOT_DATA_DIR"):
                return original_resolver(config)
            return ApplicationDataPaths(
                root=root,
                actions_file=root / "actions_library.json",
                tasks_directory=root / "tasks",
                skills_file=root / "skills" / "skill_library.json",
            )

        monkeypatch.setattr(
            ApplicationDataPaths,
            "from_config",
            staticmethod(resolve),
        )
        yield
