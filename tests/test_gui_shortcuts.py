from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.shortcuts import ShortcutDefinition, ShortcutRegistry


class ShortcutRegistryTests(unittest.TestCase):
    application: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.owner = QWidget()
        self._temporary_directory = tempfile.TemporaryDirectory()
        settings_path = Path(self._temporary_directory.name) / "shortcuts.ini"
        self.settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        self.registry = ShortcutRegistry(
            (
                ShortcutDefinition("file.save", "保存", "Ctrl+S"),
                ShortcutDefinition("file.open", "打开", "Ctrl+O"),
            ),
            settings=self.settings,
        )
        self.save_action = QAction("保存", self.owner)
        self.open_action = QAction("打开", self.owner)
        self.registry.register("file.save", self.save_action)
        self.registry.register("file.open", self.open_action)

    def tearDown(self) -> None:
        self.owner.close()
        self._temporary_directory.cleanup()

    def test_registered_actions_follow_one_central_sequence_mapping(self) -> None:
        self.registry.set_sequences(
            {
                "file.save": QKeySequence("Ctrl+Shift+S"),
                "file.open": QKeySequence("Ctrl+Shift+O"),
            }
        )

        self.assertEqual("Ctrl+Shift+S", self.save_action.shortcut().toString())
        self.assertEqual("Ctrl+Shift+O", self.open_action.shortcut().toString())

        restored = ShortcutRegistry(
            (
                ShortcutDefinition("file.save", "保存", "Ctrl+S"),
                ShortcutDefinition("file.open", "打开", "Ctrl+O"),
            ),
            settings=self.settings,
        )
        self.assertEqual("Ctrl+Shift+S", restored.sequence("file.save").toString())

    def test_duplicate_or_empty_shortcuts_are_rejected_before_actions_change(self) -> None:
        original = self.save_action.shortcut().toString()
        with self.assertRaisesRegex(ValueError, "相同快捷键"):
            self.registry.set_sequences(
                {
                    "file.save": QKeySequence("Ctrl+S"),
                    "file.open": QKeySequence("Ctrl+S"),
                }
            )
        with self.assertRaisesRegex(ValueError, "必须设置快捷键"):
            self.registry.set_sequences(
                {
                    "file.save": QKeySequence(),
                    "file.open": QKeySequence("Ctrl+O"),
                }
            )

        self.assertEqual(original, self.save_action.shortcut().toString())
