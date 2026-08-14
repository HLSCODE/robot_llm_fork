from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.configuration.data_paths import ApplicationDataPaths


@pytest.fixture(autouse=True)
def isolate_default_application_data(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Keep service-construction tests away from workstation user data."""
    original_resolver = ApplicationDataPaths.from_settings
    with TemporaryDirectory(
        prefix=".pytest-application-data-",
        dir=Path.cwd(),
    ) as temporary_directory:
        root = Path(temporary_directory)

        def resolve(settings) -> ApplicationDataPaths:
            if settings.robot_data_dir != "data":
                return original_resolver(settings)
            return ApplicationDataPaths(
                root=root,
                actions_directory=root / "actions",
                workflows_directory=root / "workflows",
                workflow_drafts_directory=root / "drafts",
                skills_directory=root / "skills",
                trajectories_directory=root / "trajectories",
            )

        monkeypatch.setattr(
            ApplicationDataPaths,
            "from_settings",
            staticmethod(resolve),
        )
        yield
