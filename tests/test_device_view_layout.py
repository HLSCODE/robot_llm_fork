from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from src.gui.views.device import DevicePoseView


class DevicePoseViewLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_pose_controls_follow_linux_font_metrics_without_clipping(self) -> None:
        view = DevicePoseView()
        font = view.font()
        font.setPointSize(16)
        view.setFont(font)
        view.resize(380, 300)
        view.render_pose(
            "robot1",
            "X:286.2 Y:-284.0 Z:352.7 mm | RX:87.8 RY:27.0 RZ:-12.1°",
        )
        view.show()
        self.application.processEvents()

        refresh = view.findChild(QPushButton, "poseRefreshButton")
        copy = view.findChild(QPushButton, "robot1PoseCopyButton")
        value = view.findChild(QLabel, "robot1PoseValue")
        assert refresh is not None
        assert copy is not None
        assert value is not None
        self.assertGreaterEqual(refresh.height(), refresh.sizeHint().height())
        self.assertGreaterEqual(copy.height(), copy.sizeHint().height())
        self.assertTrue(value.wordWrap())
        self.assertGreater(value.height(), value.fontMetrics().height())

        view.close()


if __name__ == "__main__":
    unittest.main()
