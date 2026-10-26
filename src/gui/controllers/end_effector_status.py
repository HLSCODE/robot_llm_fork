"""Bounded, non-overlapping changer queries on a background worker."""

from concurrent.futures import Future, ThreadPoolExecutor
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer

from ...application.end_effectors import EndEffectorService
from ..views.device import DeviceHealthView


class EndEffectorStatusController(QObject):
    def __init__(
        self, service: EndEffectorService, view: DeviceHealthView, parent: QObject
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._view = view
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="changer-status")
        self._future: Future[list[dict[str, Any]]] | None = None
        self._closing = False
        self._states: list[dict[str, Any]] = []
        self._last_samples: dict[str, float] = {}
        self._stale = False
        settings = service.routing.settings
        labels = {tool.id: tool.label for tool in getattr(settings, "tools", ())}
        self._mounts: list[dict[str, Any]] = [
            {
                "arm": m.arm,
                "mode": m.mode,
                "tool": m.tool,
                "tool_label": labels.get(m.tool, m.tool),
                "tools": m.tools,
                "tool_labels": tuple(labels.get(identity, identity) for identity in m.tools),
                "changer": m.changer,
                "unavailable": service.routing.unavailable_reason(m.tool)
                if m.mode == "fixed"
                else "",
            }
            for m in settings.mounts
        ]
        view.render_end_effectors(self._mounts, [])
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._next_query = 0.0
        self._timer.timeout.connect(self._tick)
        if any(m.mode == "exchangeable" for m in settings.mounts):
            self._timer.start()

    def _tick(self) -> None:
        if self._closing:
            return
        if self._future is not None and self._future.done():
            future, self._future = self._future, None
            try:
                states = future.result()
            except Exception as error:
                states = [
                    {"arm": m["arm"], "locked": "unknown", "power": "unknown", "error": str(error)}
                    for m in self._mounts
                ]
            for state in states:
                arm = state["arm"]
                if state.get("sampled_at") is not None:
                    self._last_samples[arm] = state["sampled_at"]
                else:
                    state["sampled_at"] = self._last_samples.get(arm)
            self._states = states
            self._stale = False
            self._view.render_end_effectors(self._mounts, self._states)
            self._next_query = (
                time.monotonic() + self._service.routing.settings.status_poll_interval_seconds
            )
        if self._future is None and time.monotonic() >= self._next_query:
            self._future = self._worker.submit(self._service.read_changer_states)
        expiry = max(10.0, self._service.routing.settings.status_poll_interval_seconds * 3)
        if not self._stale and any(
            state.get("sampled_at") is not None and time.time() - state["sampled_at"] > expiry
            for state in self._states
        ):
            self._stale = True
            self._view.render_end_effectors(
                self._mounts,
                [
                    dict(state, locked="unknown", power="unknown", error="状态已过期，等待重新采样")
                    for state in self._states
                ],
            )

    def prepare_shutdown(self) -> None:
        self._closing = True
        self._timer.stop()

    def shutdown(self) -> None:
        self._worker.shutdown(wait=True, cancel_futures=True)
