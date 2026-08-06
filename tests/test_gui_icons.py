from __future__ import annotations

import unittest

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from src.gui.icons import IconName, themed_icon


class GuiIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_all_compiled_svg_resources_render_at_common_sizes(self) -> None:
        widget = QWidget()
        for name in IconName:
            icon = themed_icon(widget, name, size=24)
            self.assertFalse(icon.isNull(), name.value)
            for size in (QSize(16, 16), QSize(24, 24), QSize(32, 32)):
                self.assertFalse(icon.pixmap(size).isNull(), (name.value, size))

    def test_monochrome_svg_uses_requested_palette_color(self) -> None:
        widget = QWidget()

        red = themed_icon(
            widget,
            IconName.TASKS,
            color=QColor("#ff0000"),
        ).pixmap(QSize(20, 20)).toImage()
        blue = themed_icon(
            widget,
            IconName.TASKS,
            color=QColor("#0000ff"),
        ).pixmap(QSize(20, 20)).toImage()

        self.assertTrue(_contains_dominant_channel(red, channel="red"))
        self.assertTrue(_contains_dominant_channel(blue, channel="blue"))


def _contains_dominant_channel(image, *, channel: str) -> bool:
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            if channel == "red" and color.red() > color.blue():
                return True
            if channel == "blue" and color.blue() > color.red():
                return True
    return False


if __name__ == "__main__":
    unittest.main()
