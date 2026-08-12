from __future__ import annotations

from typing import ClassVar
import unittest

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel

from src.gui.view_models.models import DeviceViewState
from src.gui.views.device import DeviceHealthView


class DeviceHealthViewTests(unittest.TestCase):
    application: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_rendered_statuses_keep_device_names_and_match_summary_devices(
        self,
    ) -> None:
        view = DeviceHealthView()
        view.render_state(
            DeviceViewState(
                robot_ready=True,
                body_ready=False,
                pipette_ready=True,
                relay_ready=False,
            )
        )

        expected_text = {
            "robot_status_text": "机械臂: 已连接",
            "body_status_text": "身体轴: 未连接",
            "pipette_status_text": "移液器: 已连接",
            "relay_status_text": "继电器: 未连接",
        }
        for object_name, text in expected_text.items():
            label = view.findChild(QLabel, object_name)
            self.assertIsNotNone(label)
            assert label is not None
            self.assertEqual(text, label.text())

        view.deleteLater()
        QApplication.processEvents()

    def test_statuses_use_two_columns_without_forcing_all_devices_into_one_row(
        self,
    ) -> None:
        view = DeviceHealthView()
        view.resize(460, 300)
        view.show()
        QApplication.processEvents()

        labels = {
            key: view.findChild(QLabel, f"{key}_status_text")
            for key in ("robot", "body", "pipette", "relay")
        }
        self.assertTrue(all(label is not None for label in labels.values()))
        robot = labels["robot"]
        body = labels["body"]
        pipette = labels["pipette"]
        relay = labels["relay"]
        assert robot is not None
        assert body is not None
        assert pipette is not None
        assert relay is not None
        positions = {
            key: label.mapTo(view, QPoint(0, 0))
            for key, label in (
                ("robot", robot),
                ("body", body),
                ("pipette", pipette),
                ("relay", relay),
            )
        }
        self.assertEqual(positions["robot"].y(), positions["body"].y())
        self.assertEqual(positions["pipette"].y(), positions["relay"].y())
        self.assertGreater(positions["pipette"].y(), positions["robot"].y())
        self.assertGreater(positions["body"].x(), positions["robot"].x())

        view.close()
        view.deleteLater()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
