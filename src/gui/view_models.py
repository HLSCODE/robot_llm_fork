from __future__ import annotations

from dataclasses import dataclass

from ..application import DeviceManagementService, ExecutionService
from ..device_runtime.ids import BODY_AXIS, PIPETTE, RELAY_BANK, ROBOT_SYSTEM
from ..execution import ExecutionState


@dataclass(frozen=True, slots=True)
class DeviceViewState:
    robot_ready: bool
    body_ready: bool
    pipette_ready: bool
    relay_ready: bool


class DeviceViewModel:
    """Derive GUI device state from the runtime-owned snapshots."""

    def __init__(self, devices: DeviceManagementService) -> None:
        self._devices = devices

    def snapshot(self) -> DeviceViewState:
        statuses = self._devices.status()
        return DeviceViewState(
            robot_ready=_is_ready(statuses, ROBOT_SYSTEM),
            body_ready=_is_ready(statuses, BODY_AXIS),
            pipette_ready=_is_ready(statuses, PIPETTE),
            relay_ready=_is_ready(statuses, RELAY_BANK),
        )


@dataclass(frozen=True, slots=True)
class ExecutionViewState:
    state: ExecutionState
    active: bool
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    pause_button_text: str


class ExecutionViewModel:
    """Expose execution state and controls without duplicating runtime flags."""

    def __init__(self, execution: ExecutionService) -> None:
        self._execution = execution

    def snapshot(self) -> ExecutionViewState:
        snapshot = self._execution.snapshot()
        state = snapshot.state
        return ExecutionViewState(
            state=state,
            active=snapshot.active,
            can_pause=state is ExecutionState.RUNNING,
            can_resume=state is ExecutionState.PAUSED,
            can_cancel=snapshot.active,
            pause_button_text="▶ 继续" if state is ExecutionState.PAUSED else "⏸ 暂停",
        )

    def toggle_pause(self) -> ExecutionViewState:
        state = self.snapshot()
        if state.can_resume:
            self._execution.resume()
        elif state.can_pause:
            self._execution.pause()
        return self.snapshot()

    def cancel(self) -> ExecutionViewState:
        state = self.snapshot()
        if state.can_cancel:
            self._execution.cancel()
        return self.snapshot()


def _is_ready(statuses: dict[str, dict[str, object]], device_id: str) -> bool:
    return statuses.get(device_id, {}).get("ready") is True
