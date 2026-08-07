from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QSettings

from src.gui.workbench_layout import (
    QSettingsWorkbenchLayoutStore,
    WORKBENCH_LAYOUT_SCHEMA_VERSION,
    WORKBENCH_LAYOUT_SETTINGS_KEY,
    WorkbenchLayoutState,
)


class WorkbenchLayoutStoreTests(unittest.TestCase):
    def test_round_trip_preserves_versioned_layout(self) -> None:
        with TemporaryDirectory() as directory:
            store = _store(Path(directory) / "settings.ini")
            expected = WorkbenchLayoutState(
                schema_version=WORKBENCH_LAYOUT_SCHEMA_VERSION,
                side_page="assistant",
                side_visible=True,
                side_width=340,
                panel_page="logs",
                panel_visible=True,
            )

            store.save(expected)

            self.assertEqual(expected, store.load().state)

    def test_invalid_json_is_removed_and_reported_as_recovered(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "settings.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue(WORKBENCH_LAYOUT_SETTINGS_KEY, "{broken")
            settings.sync()
            store = QSettingsWorkbenchLayoutStore(settings)

            result = store.load()

            self.assertTrue(result.recovered)
            self.assertIsNone(result.state)
            self.assertIsNotNone(result.reason)
            self.assertFalse(settings.contains(WORKBENCH_LAYOUT_SETTINGS_KEY))

    def test_unknown_schema_or_fields_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "settings.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue(
                WORKBENCH_LAYOUT_SETTINGS_KEY,
                json.dumps(
                    {
                        "schema_version": 999,
                        "side_page": "tasks",
                        "side_visible": True,
                        "side_width": 280,
                        "panel_page": "devices",
                        "panel_visible": False,
                        "unexpected": True,
                    }
                ),
            )
            settings.sync()

            result = QSettingsWorkbenchLayoutStore(settings).load()

            self.assertTrue(result.recovered)
            self.assertIsNone(result.state)


def _store(path: Path) -> QSettingsWorkbenchLayoutStore:
    return QSettingsWorkbenchLayoutStore(QSettings(str(path), QSettings.Format.IniFormat))


if __name__ == "__main__":
    unittest.main()
