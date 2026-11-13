from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from src.gui.icons import IconName, themed_icon
from src.gui.theme import DARK_COLORS, LIGHT_COLORS, build_palette
from src.gui.views.workflow import TaskLibraryListWidget


def test_theme_refresh_preserves_remote_and_local_task_icons():
    app = QApplication.instance() or QApplication([])
    tasks = TaskLibraryListWidget()
    remote = QListWidgetItem("远程任务", tasks)
    local = QListWidgetItem("本地任务", tasks)
    for item, allowed in ((remote, True), (local, False)):
        item.setData(Qt.ItemDataRole.UserRole, item.text() + ".workflow.json")
        tasks.set_task_access(item, allow_remote=allowed)
    tasks.setCurrentItem(remote)
    for colors in (DARK_COLORS, LIGHT_COLORS, DARK_COLORS):
        tasks.setPalette(build_palette(colors))
        app.sendEvent(tasks, QEvent(QEvent.Type.ApplicationPaletteChange))
        app.processEvents()
        for item, name in ((remote, IconName.TASK_REMOTE), (local, IconName.TASK_LOCAL)):
            expected = themed_icon(tasks, name, size=20, color=tasks.palette().highlight().color())
            assert item.icon().pixmap(20, 20).toImage() == expected.pixmap(20, 20).toImage()
            assert item.data(Qt.ItemDataRole.UserRole) == item.text() + ".workflow.json"
        assert tasks.currentItem() is remote
    tasks.close()
