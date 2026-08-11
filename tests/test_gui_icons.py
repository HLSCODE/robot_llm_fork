from __future__ import annotations

import unittest
from typing import ClassVar

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QWidget

from src.domain.models import ActionDefinition, ActionType
from src.gui.icons import IconName, action_icon, themed_icon


class GuiIconTests(unittest.TestCase):
    application: ClassVar[QApplication]

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

    def test_application_icon_resource_renders_at_desktop_sizes(self) -> None:
        for resource in (
            ":/app/app-icon-dark.png",
            ":/app/app-icon-light.png",
        ):
            icon = QIcon(resource)
            self.assertFalse(icon.isNull(), resource)
            for size in (
                QSize(16, 16),
                QSize(32, 32),
                QSize(48, 48),
                QSize(256, 256),
            ):
                self.assertFalse(icon.pixmap(size).isNull(), (resource, size))

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

    def test_motion_subtypes_have_distinct_robot_semantic_icons(self) -> None:
        icons = {
            action_icon(_action(ActionType.MOVE, {"目标": "机械臂"})),
            action_icon(_action(ActionType.MOVE, {"目标": "机械臂相对"})),
            action_icon(_action(ActionType.MOVE, {"目标": "身体"})),
            action_icon(_action(ActionType.BASE_MOVE, {"move_mode": "position"})),
        }

        self.assertEqual(4, len(icons))

    def test_manipulator_subtypes_share_family_but_keep_distinct_icons(self) -> None:
        executors = (
            "快换手",
            "继电器",
            "夹爪",
            "吸液枪",
            "颈部",
            "右臂转圈注液",
            "加粉装置",
            "智能加粉",
            "表情屏",
        )
        icons = {
            action_icon(_action(ActionType.MANIPULATE, {"执行器": executor}))
            for executor in executors
        }

        self.assertEqual(len(executors), len(icons))
        self.assertNotIn(IconName.ACTION_MANIPULATE, icons)


def _action(
    action_type: ActionType,
    parameters: dict[str, object],
) -> ActionDefinition:
    return ActionDefinition(
        id="action-id",
        name="action",
        type=action_type,
        parameters=parameters,
    )


def _contains_dominant_channel(image: QImage, *, channel: str) -> bool:
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
