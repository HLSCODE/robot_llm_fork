from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from threading import RLock
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import uuid4

from ..devices import (
    ArmId,
    ArmState,
    ArmStateReader,
    ArmTelemetryReader,
    DeviceOperationError,
    DeviceRuntime,
    DigitalOutputs,
    GripperControl,
    JointVector,
    Pipette,
    ResourceArbiter,
    ResourceLease,
    RobotTeleoperation,
    StopMode,
    TrajectoryControl,
    TrajectorySaveResult,
)
from ..devices.runtime.errors import normalize_device_error
from ..configuration.settings import ApplicationSettings
from ..devices.runtime.ids import PIPETTE, RELAY_BANK, ROBOT_SYSTEM
from ..execution import (
    ExecutionHandle,
    ExecutionListener,
    ExecutionManager,
    ExecutionSnapshot,
)
from ..llm import LLMRegistry
from .camera_access import CameraAccessService
from ..vision.service import VisionService
from .command_runtime import CommandRuntime
from .composition import CompositionService
from .safety import SafetyService
from .localization import LocalizationService
from .teleoperation import TeleoperationService
from .task_composer import TaskComposerService

if TYPE_CHECKING:
    from .data_collection import DataCollectionService


_ResultT = TypeVar("_ResultT")


def _device_operation(
    device_id: str,
    operation: str,
    callback: Callable[[], _ResultT],
) -> _ResultT:
    try:
        return callback()
    except DeviceOperationError:
        raise
    except Exception as exc:
        raise normalize_device_error(
            exc,
            device_id=device_id,
            operation=operation,
        ) from exc


class ExecutionService:
    """Application entry for every sequence execution source."""

    def __init__(self, manager: ExecutionManager) -> None:
        self._manager = manager

    def start(
        self,
        sequence: Sequence[Any],
        *,
        origin: str,
        listener: ExecutionListener | None = None,
    ) -> ExecutionHandle:
        return self._manager.submit(
            sequence,
            origin=origin,
            listener=listener,
        )

    def pause(self) -> None:
        self._manager.pause()

    def resume(self) -> None:
        self._manager.resume()

    def cancel(self) -> None:
        self._manager.cancel()

    def snapshot(self) -> ExecutionSnapshot:
        return self._manager.snapshot()

    def wait(self, timeout: float | None = None) -> ExecutionSnapshot:
        return self._manager.wait(timeout=timeout)


class DeviceManagementService:
    """Application entry for device lifecycle and status."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        resources: ResourceArbiter,
        safety: SafetyService,
    ) -> None:
        self._runtime = runtime
        self._resources = resources
        self._safety = safety

    def initialize(self, device_id: str) -> dict[str, Any]:
        with self._lifecycle_lease("initialize", (device_id,)):
            _device_operation(
                device_id,
                "device.initialize",
                lambda: self._runtime.initialize(device_id),
            )
            return self._snapshot_dict(device_id)

    def initialize_many(
        self,
        device_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        selected = tuple(dict.fromkeys(device_ids))
        if not selected:
            return {}
        with self._lifecycle_lease("initialize-many", selected):
            for device_id in selected:
                _device_operation(
                    device_id,
                    "device.initialize",
                    partial(self._runtime.initialize, device_id),
                )
            return {
                device_id: self._snapshot_dict(device_id)
                for device_id in selected
            }

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            snapshot.device_id: {
                "state": snapshot.state.value,
                "ready": snapshot.ready,
                "capabilities": [
                    capability.value for capability in snapshot.capabilities
                ],
                "error": snapshot.error,
                "error_category": snapshot.error_category,
                "raw_error_code": snapshot.raw_error_code,
            }
            for snapshot in self._runtime.snapshots()
        }

    def is_ready(self, device_id: str) -> bool:
        """Return whether a registered device is ready without exposing it."""
        return self._runtime.snapshot(device_id).ready

    def shutdown_all(self, timeout: float = 10.0) -> dict[str, str]:
        report = self._safety.stop(
            StopMode.CONTROLLED,
            wait_timeout_seconds=timeout,
        )
        if report.execution_after.active:
            raise TimeoutError(
                "execution did not stop before device shutdown"
            )
        errors = {
            f"safety:{index}": error
            for index, error in enumerate(report.errors, start=1)
        }
        device_ids = self._runtime.registered_device_ids()
        with self._lifecycle_lease("shutdown", device_ids):
            errors.update(self._runtime.shutdown_all())
        return errors

    def _lifecycle_lease(
        self,
        operation: str,
        device_ids: Sequence[str],
    ) -> ResourceLease:
        return self._resources.acquire(
            owner_id=f"device-lifecycle:{operation}:{uuid4().hex}",
            resources=tuple(device_ids),
        )

    def _snapshot_dict(self, device_id: str) -> dict[str, Any]:
        snapshot = self._runtime.snapshot(device_id)
        return {
            "state": snapshot.state.value,
            "ready": snapshot.ready,
            "capabilities": [
                capability.value for capability in snapshot.capabilities
            ],
            "error": snapshot.error,
            "error_category": snapshot.error_category,
            "raw_error_code": snapshot.raw_error_code,
        }


class ManualControlService:
    """Typed direct-control use cases that still obey resource ownership."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        resources: ResourceArbiter,
    ) -> None:
        self._runtime = runtime
        self._resources = resources

    def set_gripper(
        self,
        arm: str,
        *,
        opened: bool | None = None,
        position: int | None = None,
    ) -> bool:
        if opened is None and position is None:
            raise ValueError("opened or position is required")
        arm_id = ArmId.parse(arm)
        with self._lease(ROBOT_SYSTEM, "gripper"):
            def operate_gripper() -> bool:
                gripper = self._runtime.require(
                    ROBOT_SYSTEM,
                    GripperControl,
                )
                if position is not None:
                    gripper.move_gripper(arm_id, int(position))
                elif opened:
                    gripper.open_gripper(arm_id)
                else:
                    gripper.close_gripper(arm_id)
                return True

            return _device_operation(
                ROBOT_SYSTEM,
                "gripper.set",
                operate_gripper,
            )

    def set_relay(self, channel: int, enabled: bool) -> None:
        with self._lease(RELAY_BANK, "relay"):
            _device_operation(
                RELAY_BANK,
                "relay.set_channel",
                lambda: self._runtime.require(
                    RELAY_BANK,
                    DigitalOutputs,
                ).set_channel(channel, enabled),
            )

    def initialize_pipette(self) -> bool:
        with self._lease(PIPETTE, "pipette-initialize"):
            return _device_operation(
                PIPETTE,
                "pipette.initialize",
                lambda: self._runtime.require(
                    PIPETTE,
                    Pipette,
                ).initialize(),
            )

    def eject_pipette_tip(self) -> bool:
        with self._lease(PIPETTE, "pipette-eject"):
            return _device_operation(
                PIPETTE,
                "pipette.eject_tip",
                lambda: self._runtime.require(
                    PIPETTE,
                    Pipette,
                ).eject_tip(),
            )

    def initialize_teleoperation(
        self,
        arm: str,
        joints: list[float],
    ) -> bool:
        arm_id = ArmId.parse(arm)
        with self._lease(ROBOT_SYSTEM, "teleop-initialize"):
            return _device_operation(
                ROBOT_SYSTEM,
                "robot_system.initialize_teleoperation",
                lambda: self._initialize_teleoperation(
                    arm_id,
                    joints,
                ),
            )

    def _initialize_teleoperation(
        self,
        arm_id: ArmId,
        joints: list[float],
    ) -> bool:
        teleoperation = self._runtime.require(
            ROBOT_SYSTEM,
            RobotTeleoperation,
        )
        teleoperation.initialize_teleoperation(
            arm_id,
            JointVector.from_iterable(joints),
        )
        return True

    def _lease(
        self,
        resource_id: str,
        operation: str,
    ) -> ResourceLease:
        owner_id = f"manual:{operation}:{uuid4().hex}"
        return self._resources.acquire(owner_id, (resource_id,))


class RobotQueryService:
    """Read normalized robot state without exposing a vendor SDK object."""

    def __init__(self, runtime: DeviceRuntime) -> None:
        self._runtime = runtime

    def state_reader(self) -> ArmStateReader:
        """Return the normalized read capability for a read-only session."""
        return self._runtime.require(ROBOT_SYSTEM, ArmStateReader)

    def telemetry_reader(self) -> ArmTelemetryReader:
        """Return timestamped telemetry for data acquisition."""
        return self._runtime.require(ROBOT_SYSTEM, ArmTelemetryReader)

    def read_state(self, arm: str | ArmId) -> ArmState:
        return _device_operation(
            ROBOT_SYSTEM,
            "robot_system.read_arm_state",
            lambda: self.state_reader().read_arm_state(_arm_id(arm)),
        )

    def try_read_state(self, arm: str | ArmId) -> ArmState | None:
        reader = self._runtime.get_if_ready(ROBOT_SYSTEM)
        if reader is None or not isinstance(reader, ArmStateReader):
            return None
        return reader.try_read_arm_state(_arm_id(arm))


class TrajectoryTeachingService:
    """Own the robot lease for one drag-teaching session."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        resources: ResourceArbiter,
    ) -> None:
        self._runtime = runtime
        self._resources = resources
        self._lease: ResourceLease | None = None
        self._arm: ArmId | None = None
        self._lock = RLock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._lease is not None

    def start(self, arm: str | ArmId) -> None:
        arm_id = _arm_id(arm)
        with self._lock:
            if self._lease is not None:
                raise RuntimeError("trajectory teaching is already active")
            lease = self._resources.acquire(
                f"trajectory-teaching:{arm_id.value}",
                (ROBOT_SYSTEM,),
            )
            try:
                _device_operation(
                    ROBOT_SYSTEM,
                    "trajectory.start_drag_teaching",
                    lambda: self._runtime.require(
                        ROBOT_SYSTEM,
                        TrajectoryControl,
                    ).start_drag_teaching(arm_id),
                )
            except Exception:
                lease.release()
                raise
            self._lease = lease
            self._arm = arm_id

    def stop_and_save(self, path: str) -> TrajectorySaveResult:
        with self._lock:
            arm, lease = self._required_session_unlocked()
            trajectory = _device_operation(
                ROBOT_SYSTEM,
                "trajectory.resolve",
                lambda: self._runtime.require(
                    ROBOT_SYSTEM,
                    TrajectoryControl,
                ),
            )
            _device_operation(
                ROBOT_SYSTEM,
                "trajectory.stop_drag_teaching",
                lambda: trajectory.stop_drag_teaching(arm),
            )
            self._arm = None
            self._lease = None
        try:
            return _device_operation(
                ROBOT_SYSTEM,
                "trajectory.save",
                lambda: trajectory.save_trajectory(arm, path),
            )
        finally:
            lease.release()

    def cancel(self) -> None:
        with self._lock:
            if self._lease is None or self._arm is None:
                return
            arm = self._arm
            lease = self._lease
            self._arm = None
            self._lease = None
        try:
            trajectory = self._runtime.get_if_ready(ROBOT_SYSTEM)
            if isinstance(trajectory, TrajectoryControl):
                _device_operation(
                    ROBOT_SYSTEM,
                    "trajectory.stop_drag_teaching",
                    lambda: trajectory.stop_drag_teaching(arm),
                )
        finally:
            lease.release()

    def release_after_safety_stop(self) -> None:
        """Release ownership after the robot adapter already accepted a stop."""
        with self._lock:
            lease = self._lease
            self._arm = None
            self._lease = None
        if lease is not None:
            lease.release()

    def _required_session_unlocked(
        self,
    ) -> tuple[ArmId, ResourceLease]:
        if self._arm is None or self._lease is None:
            raise RuntimeError("trajectory teaching is not active")
        return self._arm, self._lease


def _arm_id(arm: str | ArmId) -> ArmId:
    return arm if isinstance(arm, ArmId) else ArmId.parse(arm)


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    camera_access: CameraAccessService
    vision: VisionService
    localization: LocalizationService
    composition: CompositionService
    task_composer: TaskComposerService
    data_collection: DataCollectionService
    execution: ExecutionService
    devices: DeviceManagementService
    manual_control: ManualControlService
    teleoperation: TeleoperationService
    robot_query: RobotQueryService
    trajectory_teaching: TrajectoryTeachingService
    safety: SafetyService
    commands: CommandRuntime
    llm: LLMRegistry
    resources: ResourceArbiter
    simulation: bool
    settings: ApplicationSettings
