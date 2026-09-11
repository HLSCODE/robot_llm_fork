"""Native lifetime regressions run in subprocesses to protect pytest's QApplication."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("case", [
    "integer_accept", "integer_reject", "text_accept", "text_reject", "startup", "combo",
])
def test_widget_cleanup_preserves_application_screen(case: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", (
            "import runpy; "
            "runpy.run_path('tests/probes/gui_screen_lifetime.py', run_name='__main__')"
        ), case],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "12 cleanup cycles and application teardown passed" in result.stdout
