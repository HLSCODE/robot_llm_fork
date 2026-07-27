from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any
from uuid import uuid4

from ..device_runtime import (
    ArmId,
    ArmState,
    ArmStateReader,
    DeviceRuntime,
    DigitalOutputs,
    GripperControl,
    JointVector,
    Pipette,
    ResourceArbiter,
    ResourceLease,
    RobotTeleoperation,
    TrajectoryControl,
    TrajectorySaveResult,
)
from ..device_runtime.ids import PIPETTE, RELAY_BANK, ROBOT_SYSTEM
from ..execution import (
    ExecutionHandle,
    ExecutionListener,
    ExecutionManager,
    ExecutionSnapshot,
    ExecutionStateError,
)


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

    def cancel_and_wait(self, timeout: float = 10.0) -> ExecutionSnapshot:
        snapshot = self.snapshot()
        if not snapshot.active:
            return snapshot
        try:
            self.cancel()
        except ExecutionStateError:
            return self.snapshot()
        return self.wait(timeout)


class DeviceManagementService:
    """Application entry for device lifecycle and status."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        execution: ExecutionService,
        teleoperation: "TeleoperationService",
        trajectory_teaching: "TrajectoryTeachingService",
    ) -> None:
        self._runtime = runtime
        self._execution = execution
        self._teleoperation = teleoperation
        self._trajectory_teaching = trajectory_teaching

    def initialize(self, device_id: str) -> dict[str, Any]:
        self._runtime.initialize(device_id)
        return self._snapshot_dict(device_id)

    def initialize_many(
        self,
        device_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        return {
            device_id: self.initialize(device_id)
            for device_id in device_ids
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
            }
            for snapshot in self._runtime.snapshots()
        }

    def shutdown_all(self, timeout: float = 10.0) -> dict[str, str]:
        self._teleoperation.stop()
        errors: dict[str, str] = {}
        try:
            self._trajectory_teaching.cancel()
        except Exception as exc:
            errors["trajectory-teaching"] = str(exc)
        snapshot = self._execution.snapshot()
        if snapshot.active:
            final = self._execution.cancel_and_wait(timeout)
            if final.active:
                raise TimeoutError(
                    "execution did not stop before device shutdown"
                )
        errors.update(self._runtime.shutdown_all())
        return errors

    def _snapshot_dict(self, device_id: str) -> dict[str, Any]:
        snapshot = self._runtime.snapshot(device_id)
        return {
            "state": snapshot.state.value,
            "ready": snapshot.ready,
            "capabilities": [
                capability.value for capability in snapshot.capabilities
            ],
            "error": snapshot.error,
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
            gripper = self._runtime.require(ROBOT_SYSTEM, GripperControl)
            if position is not None:
                gripper.move_gripper(arm_id, int(position))
            elif opened:
                gripper.open_gripper(arm_id)
            else:
                gripper.close_gripper(arm_id)
            return True

    def set_relay(self, channel: int, enabled: bool) -> None:
        with self._lease(RELAY_BANK, "relay"):
            relay = self._runtime.require(RELAY_BANK, DigitalOutputs)
            relay.set_channel(channel, enabled)

    def initialize_pipette(self) -> bool:
        with self._lease(PIPETTE, "pipette-initialize"):
            pipette = self._runtime.require(PIPETTE, Pipette)
            return pipette.initialize()

    def eject_pipette_tip(self) -> bool:
        with self._lease(PIPETTE, "pipette-eject"):
            pipette = self._runtime.require(PIPETTE, Pipette)
            return pipette.eject_tip()

    def initialize_teleoperation(
        self,
        arm: str,
        joints: list[float],
    ) -> bool:
        arm_id = ArmId.parse(arm)
        with self._lease(ROBOT_SYSTEM, "teleop-initialize"):
            teleoperation = self._runtime.require(
                ROBOT_SYSTEM,
                RobotTeleoperation,
            )
            teleoperation.initialize_teleoperation(
                arm_id,
                JointVector.from_iterable(joints),
            )
            return True

    def _lease(self, resource_id: str, operation: str):
        owner_id = f"manual:{operation}:{uuid4().hex}"
        return self._resources.acquire(owner_id, (resource_id,))


class TeleoperationService:
    """Hold a robot resource lease for the whole teleoperation session."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        resources: ResourceArbiter,
    ) -> None:
        self._runtime = runtime
        self._resources = resources
        self._lease: ResourceLease | None = None
        self._lock = RLock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._lease is not None

    def start(self) -> None:
        with self._lock:
            if self._lease is not None:
                return
            lease = self._resources.acquire(
                "teleoperation",
                (ROBOT_SYSTEM,),
            )
            try:
                self._runtime.require(ROBOT_SYSTEM, RobotTeleoperation)
            except Exception:
                lease.release()
                raise
            self._lease = lease

    def stop(self) -> None:
        with self._lock:
            lease = self._lease
            self._lease = None
        if lease is not None:
            lease.release()

    def follow(
        self,
        arm: str,
        joints: list[float],
        *,
        follow: bool,
        trajectory_mode: int,
    ) -> bool:
        with self._lock:
            self._require_active_unlocked()
            teleoperation = self._runtime.require(
                ROBOT_SYSTEM,
                RobotTeleoperation,
            )
            teleoperation.follow_joints(
                ArmId.parse(arm),
                JointVector.from_iterable(joints),
                follow=follow,
                trajectory_mode=trajectory_mode,
            )
            return True

    def set_gripper(self, arm: str, position: int) -> bool:
        with self._lock:
            self._require_active_unlocked()
            gripper = self._runtime.require(ROBOT_SYSTEM, GripperControl)
            gripper.move_gripper(ArmId.parse(arm), int(position))
            return True

    def _require_active_unlocked(self) -> None:
        if self._lease is None:
            raise RuntimeError("teleoperation session is not active")


class RobotQueryService:
    """Read normalized robot state without exposing a vendor SDK object."""

    def __init__(self, runtime: DeviceRuntime) -> None:
        self._runtime = runtime

    def read_state(self, arm: str | ArmId) -> ArmState:
        reader = self._runtime.require(ROBOT_SYSTEM, ArmStateReader)
        return reader.read_arm_state(_arm_id(arm))

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
                trajectory = self._runtime.require(
                    ROBOT_SYSTEM,
                    TrajectoryControl,
                )
                trajectory.start_drag_teaching(arm_id)
            except Exception:
                lease.release()
                raise
            self._lease = lease
            self._arm = arm_id

    def stop_and_save(self, path: str) -> TrajectorySaveResult:
        with self._lock:
            arm, lease = self._required_session_unlocked()
            trajectory = self._runtime.require(
                ROBOT_SYSTEM,
                TrajectoryControl,
            )
            trajectory.stop_drag_teaching(arm)
            self._arm = None
            self._lease = None
        try:
            return trajectory.save_trajectory(arm, path)
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
                trajectory.stop_drag_teaching(arm)
        finally:
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
    execution: ExecutionService
    devices: DeviceManagementService
    manual_control: ManualControlService
    teleoperation: TeleoperationService
    robot_query: RobotQueryService
    trajectory_teaching: TrajectoryTeachingService
    device_runtime: DeviceRuntime
    resources: ResourceArbiter
    simulation: bool
