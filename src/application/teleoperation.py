"""Application-owned teleoperation sessions and robot resource lease."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from threading import RLock
import time

from ..device_runtime import (
    ArmId,
    DeviceRuntime,
    GripperControl,
    JointVector,
    ResourceArbiter,
    ResourceLease,
    RobotTeleoperation,
)
from ..device_runtime.ids import ROBOT_SYSTEM


DATA_COLLECTION_TELEOPERATION_OWNER = "data-collection"
WEBSOCKET_TELEOPERATION_OWNER_PREFIX = "websocket:"


def websocket_teleoperation_owner(client_id: str) -> str:
    normalized = client_id.strip()
    if not normalized:
        raise ValueError("websocket client id must not be empty")
    return f"{WEBSOCKET_TELEOPERATION_OWNER_PREFIX}{normalized}"


@dataclass(frozen=True, slots=True)
class TeleoperationArmSnapshot:
    arm: ArmId
    command_count: int
    last_command_at: float | None
    last_gripper_position: int | None


@dataclass(frozen=True, slots=True)
class TeleoperationOwnerSnapshot:
    owner_id: str
    started_at: float
    arms: tuple[TeleoperationArmSnapshot, ...]

    def controls(self, arm: str | ArmId) -> bool:
        arm_id = _arm_id(arm)
        return any(item.arm is arm_id for item in self.arms)

    def command_count(self, arm: str | ArmId) -> int:
        arm_id = _arm_id(arm)
        return next(
            (item.command_count for item in self.arms if item.arm is arm_id),
            0,
        )


@dataclass(frozen=True, slots=True)
class TeleoperationSnapshot:
    owners: tuple[TeleoperationOwnerSnapshot, ...]

    @property
    def active(self) -> bool:
        return bool(self.owners)

    @property
    def active_arms(self) -> tuple[ArmId, ...]:
        return tuple(
            arm
            for arm in ArmId
            if any(owner.controls(arm) for owner in self.owners)
        )

    def owner(self, owner_id: str) -> TeleoperationOwnerSnapshot | None:
        return next(
            (owner for owner in self.owners if owner.owner_id == owner_id),
            None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "active_arms": [arm.value for arm in self.active_arms],
            "owners": [
                {
                    "owner_id": owner.owner_id,
                    "arms": [
                        {
                            "arm": arm.arm.value,
                            "command_count": arm.command_count,
                            "has_received_command": (
                                arm.last_command_at is not None
                            ),
                            "last_gripper_position": (
                                arm.last_gripper_position
                            ),
                        }
                        for arm in owner.arms
                    ],
                }
                for owner in self.owners
            ],
        }


@dataclass(frozen=True, slots=True)
class TeleoperationCommandResult:
    owner_id: str
    arm: ArmId
    command_count: int
    applied: bool = True


@dataclass(slots=True)
class _ArmSession:
    command_count: int = 0
    last_command_at: float | None = None
    last_gripper_position: int | None = None


@dataclass(slots=True)
class _OwnerSession:
    started_at: float
    arms: dict[ArmId, _ArmSession] = field(default_factory=dict)


class TeleoperationService:
    """Own teleoperation state, operations, and the shared robot lease."""

    def __init__(
        self,
        runtime: DeviceRuntime,
        resources: ResourceArbiter,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._resources = resources
        self._clock = clock
        self._lease: ResourceLease | None = None
        self._owners: dict[str, _OwnerSession] = {}
        self._lock = RLock()
        self._operation_lock = RLock()

    @property
    def active(self) -> bool:
        return self.snapshot().active

    def snapshot(self) -> TeleoperationSnapshot:
        with self._lock:
            return TeleoperationSnapshot(
                owners=tuple(
                    self._owner_snapshot(owner_id, session)
                    for owner_id, session in sorted(self._owners.items())
                )
            )

    def start(
        self,
        owner_id: str,
        arms: Iterable[str | ArmId],
    ) -> TeleoperationOwnerSnapshot:
        normalized_owner = _owner_id(owner_id)
        arm_ids = _arm_ids(arms)
        with self._lock:
            if self._lease is None:
                lease = self._resources.acquire(
                    "teleoperation",
                    (ROBOT_SYSTEM,),
                )
                try:
                    self._runtime.require(
                        ROBOT_SYSTEM,
                        RobotTeleoperation,
                    )
                except Exception:
                    lease.release()
                    raise
                self._lease = lease
            session = self._owners.setdefault(
                normalized_owner,
                _OwnerSession(started_at=self._clock()),
            )
            for arm in arm_ids:
                session.arms.setdefault(arm, _ArmSession())
            return self._owner_snapshot(normalized_owner, session)

    def stop(
        self,
        owner_id: str,
        arms: Iterable[str | ArmId] | None = None,
    ) -> TeleoperationOwnerSnapshot | None:
        normalized_owner = _owner_id(owner_id)
        with self._lock:
            session = self._owners.get(normalized_owner)
            if session is None:
                return None
            stopped_arms = (
                tuple(session.arms)
                if arms is None
                else _arm_ids(arms)
            )
            stopped = _OwnerSession(
                started_at=session.started_at,
                arms={
                    arm: session.arms[arm]
                    for arm in stopped_arms
                    if arm in session.arms
                },
            )
            for arm in stopped_arms:
                session.arms.pop(arm, None)
            if not session.arms:
                self._owners.pop(normalized_owner, None)
            lease = self._take_unused_lease_unlocked()
        self._release_after_operations(lease)
        if not stopped.arms:
            return None
        return self._owner_snapshot(normalized_owner, stopped)

    def stop_all(self) -> None:
        with self._lock:
            self._owners.clear()
            lease = self._lease
            self._lease = None
        self._release_after_operations(lease)

    def release_after_safety_stop(self) -> None:
        """Release all state without waiting on interrupted device I/O."""
        with self._lock:
            self._owners.clear()
            lease = self._lease
            self._lease = None
        if lease is not None:
            lease.release()

    def follow(
        self,
        owner_id: str,
        arm: str | ArmId,
        joints: list[float],
        *,
        follow: bool,
        trajectory_mode: int,
    ) -> TeleoperationCommandResult:
        normalized_owner = _owner_id(owner_id)
        arm_id = _arm_id(arm)
        with self._lock:
            self._require_arm_unlocked(
                normalized_owner,
                arm_id,
            )
            teleoperation = self._runtime.require(
                ROBOT_SYSTEM,
                RobotTeleoperation,
            )
            self._operation_lock.acquire()
        try:
            teleoperation.follow_joints(
                arm_id,
                JointVector.from_iterable(joints),
                follow=follow,
                trajectory_mode=trajectory_mode,
            )
        finally:
            self._operation_lock.release()
        with self._lock:
            current = self._require_arm_unlocked(normalized_owner, arm_id)
            current.command_count += 1
            current.last_command_at = self._clock()
            return TeleoperationCommandResult(
                owner_id=normalized_owner,
                arm=arm_id,
                command_count=current.command_count,
            )

    def set_gripper(
        self,
        owner_id: str,
        arm: str | ArmId,
        position: int,
    ) -> TeleoperationCommandResult:
        normalized_owner = _owner_id(owner_id)
        arm_id = _arm_id(arm)
        normalized_position = int(position)
        with self._lock:
            arm_session = self._require_arm_unlocked(
                normalized_owner,
                arm_id,
            )
            if arm_session.last_gripper_position == normalized_position:
                return TeleoperationCommandResult(
                    owner_id=normalized_owner,
                    arm=arm_id,
                    command_count=arm_session.command_count,
                    applied=False,
                )
            gripper = self._runtime.require(ROBOT_SYSTEM, GripperControl)
            self._operation_lock.acquire()
        try:
            gripper.move_gripper(arm_id, normalized_position)
        finally:
            self._operation_lock.release()
        with self._lock:
            current = self._require_arm_unlocked(normalized_owner, arm_id)
            current.last_gripper_position = normalized_position
            return TeleoperationCommandResult(
                owner_id=normalized_owner,
                arm=arm_id,
                command_count=current.command_count,
            )

    def expire_stale_owners(
        self,
        *,
        owner_prefix: str,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        if timeout_seconds <= 0:
            raise ValueError("teleoperation watchdog timeout must be positive")
        now = self._clock()
        with self._lock:
            stale = tuple(
                owner_id
                for owner_id, session in self._owners.items()
                if owner_id.startswith(owner_prefix)
                and now - _last_activity(session) >= timeout_seconds
            )
            for owner_id in stale:
                self._owners.pop(owner_id, None)
            lease = self._take_unused_lease_unlocked()
        self._release_after_operations(lease)
        return stale

    def _require_arm_unlocked(
        self,
        owner_id: str,
        arm: ArmId,
    ) -> _ArmSession:
        session = self._owners.get(owner_id)
        if session is None:
            raise RuntimeError(
                f"teleoperation owner '{owner_id}' is not active"
            )
        try:
            return session.arms[arm]
        except KeyError as exc:
            raise RuntimeError(
                f"teleoperation owner '{owner_id}' does not control "
                f"arm '{arm.value}'"
            ) from exc

    def _take_unused_lease_unlocked(self) -> ResourceLease | None:
        if self._owners:
            return None
        lease = self._lease
        self._lease = None
        return lease

    def _release_after_operations(
        self,
        lease: ResourceLease | None,
    ) -> None:
        if lease is None:
            return
        with self._operation_lock:
            pass
        lease.release()

    @staticmethod
    def _owner_snapshot(
        owner_id: str,
        session: _OwnerSession,
    ) -> TeleoperationOwnerSnapshot:
        return TeleoperationOwnerSnapshot(
            owner_id=owner_id,
            started_at=session.started_at,
            arms=tuple(
                TeleoperationArmSnapshot(
                    arm=arm,
                    command_count=arm_session.command_count,
                    last_command_at=arm_session.last_command_at,
                    last_gripper_position=(
                        arm_session.last_gripper_position
                    ),
                )
                for arm, arm_session in sorted(
                    session.arms.items(),
                    key=lambda item: item[0].value,
                )
            ),
        )


def _owner_id(owner_id: str) -> str:
    normalized = owner_id.strip()
    if not normalized:
        raise ValueError("teleoperation owner id must not be empty")
    return normalized


def _arm_id(arm: str | ArmId) -> ArmId:
    return arm if isinstance(arm, ArmId) else ArmId.parse(arm)


def _arm_ids(arms: Iterable[str | ArmId]) -> tuple[ArmId, ...]:
    normalized = tuple(dict.fromkeys(_arm_id(arm) for arm in arms))
    if not normalized:
        raise ValueError("teleoperation arms must not be empty")
    return normalized


def _last_activity(session: _OwnerSession) -> float:
    arm_activity_times = tuple(
        arm.last_command_at
        if arm.last_command_at is not None
        else session.started_at
        for arm in session.arms.values()
    )
    return min(arm_activity_times, default=session.started_at)
