from __future__ import annotations

from PySide6.QtCore import QObject, QThread

from ...application.services import ManualControlService


class ZhixingGripperWorker(QThread):
    """Keep serial I/O and motion completion polling off the GUI thread."""

    def __init__(
        self,
        service: ManualControlService,
        station: int,
        percent: float,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self.station = station
        self.percent = percent
        self.error = ""

    def run(self) -> None:
        try:
            self._service.set_zhixing_gripper(self.station, self.percent)
        except Exception as exc:
            self.error = str(exc)
