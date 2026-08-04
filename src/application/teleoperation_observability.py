from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from threading import Lock

from ..devices import ArmId

teleoperation_audit_logger = logging.getLogger("audit.teleoperation")


class TeleoperationEventType(StrEnum):
    SESSION_STARTED = "session_started"
    SESSION_STOPPED = "session_stopped"
    FOLLOW_COMMAND = "follow_command"
    GRIPPER_COMMAND = "gripper_command"
    WATCHDOG_EXPIRED = "watchdog_expired"
    SAFETY_RELEASED = "safety_released"


class TeleoperationEventOutcome(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class TeleoperationAuditEvent:
    event_type: TeleoperationEventType
    outcome: TeleoperationEventOutcome
    recorded_at_seconds: float
    owner_id: str
    arms: tuple[ArmId, ...] = ()
    command_count: int | None = None
    duration_seconds: float | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.recorded_at_seconds < 0:
            raise ValueError("teleoperation audit timestamp must be non-negative")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("teleoperation audit duration must be non-negative")
        if self.command_count is not None and self.command_count < 0:
            raise ValueError("teleoperation command_count must be non-negative")
        if self.outcome is TeleoperationEventOutcome.FAILED and not self.error_code:
            raise ValueError("failed teleoperation audit event requires error_code")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_type": self.event_type.value,
            "outcome": self.outcome.value,
            "recorded_at_seconds": self.recorded_at_seconds,
            "owner_id": self.owner_id,
            "arms": [arm.value for arm in self.arms],
        }
        if self.command_count is not None:
            payload["command_count"] = self.command_count
        if self.duration_seconds is not None:
            payload["duration_seconds"] = self.duration_seconds
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload


TeleoperationAuditSink = Callable[[TeleoperationAuditEvent], None]


def log_teleoperation_audit_event(event: TeleoperationAuditEvent) -> None:
    teleoperation_audit_logger.info(
        "%s",
        json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True),
    )


@dataclass(frozen=True, slots=True)
class TeleoperationMetricsSnapshot:
    sessions_started_total: int
    sessions_stopped_total: int
    follow_commands_total: int
    gripper_commands_total: int
    commands_failed_total: int
    commands_skipped_total: int
    watchdog_expirations_total: int
    safety_releases_total: int
    command_duration_seconds_total: float
    command_duration_seconds_max: float
    command_interval_seconds_total: float
    command_interval_seconds_max: float
    command_interval_jitter_seconds_max: float
    observed_throughput_hz: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "sessions_started_total": self.sessions_started_total,
            "sessions_stopped_total": self.sessions_stopped_total,
            "follow_commands_total": self.follow_commands_total,
            "gripper_commands_total": self.gripper_commands_total,
            "commands_failed_total": self.commands_failed_total,
            "commands_skipped_total": self.commands_skipped_total,
            "watchdog_expirations_total": self.watchdog_expirations_total,
            "safety_releases_total": self.safety_releases_total,
            "command_duration_seconds_total": self.command_duration_seconds_total,
            "command_duration_seconds_max": self.command_duration_seconds_max,
            "command_interval_seconds_total": self.command_interval_seconds_total,
            "command_interval_seconds_max": self.command_interval_seconds_max,
            "command_interval_jitter_seconds_max": (
                self.command_interval_jitter_seconds_max
            ),
            "observed_throughput_hz": self.observed_throughput_hz,
        }


class TeleoperationObservability:
    """Own typed audit delivery and aggregate teleoperation timing metrics."""

    def __init__(
        self,
        sink: TeleoperationAuditSink = log_teleoperation_audit_event,
    ) -> None:
        self._sink = sink
        self._lock = Lock()
        self._sessions_started_total = 0
        self._sessions_stopped_total = 0
        self._follow_commands_total = 0
        self._gripper_commands_total = 0
        self._commands_failed_total = 0
        self._commands_skipped_total = 0
        self._watchdog_expirations_total = 0
        self._safety_releases_total = 0
        self._command_duration_seconds_total = 0.0
        self._command_duration_seconds_max = 0.0
        self._command_interval_seconds_total = 0.0
        self._command_interval_seconds_max = 0.0
        self._command_interval_jitter_seconds_max = 0.0
        self._first_command_at: float | None = None
        self._last_command_at: float | None = None
        self._last_command_interval: float | None = None
        self._observed_commands = 0

    def record(self, event: TeleoperationAuditEvent) -> None:
        with self._lock:
            self._update_metrics(event)
        try:
            self._sink(event)
        except Exception:
            teleoperation_audit_logger.exception(
                "teleoperation audit sink failed: event_type=%s",
                event.event_type.value,
            )

    def snapshot(self) -> TeleoperationMetricsSnapshot:
        with self._lock:
            throughput = 0.0
            if (
                self._observed_commands > 1
                and self._first_command_at is not None
                and self._last_command_at is not None
                and self._last_command_at > self._first_command_at
            ):
                throughput = (self._observed_commands - 1) / (
                    self._last_command_at - self._first_command_at
                )
            return TeleoperationMetricsSnapshot(
                sessions_started_total=self._sessions_started_total,
                sessions_stopped_total=self._sessions_stopped_total,
                follow_commands_total=self._follow_commands_total,
                gripper_commands_total=self._gripper_commands_total,
                commands_failed_total=self._commands_failed_total,
                commands_skipped_total=self._commands_skipped_total,
                watchdog_expirations_total=self._watchdog_expirations_total,
                safety_releases_total=self._safety_releases_total,
                command_duration_seconds_total=self._command_duration_seconds_total,
                command_duration_seconds_max=self._command_duration_seconds_max,
                command_interval_seconds_total=self._command_interval_seconds_total,
                command_interval_seconds_max=self._command_interval_seconds_max,
                command_interval_jitter_seconds_max=(
                    self._command_interval_jitter_seconds_max
                ),
                observed_throughput_hz=throughput,
            )

    def _update_metrics(self, event: TeleoperationAuditEvent) -> None:
        if (
            event.event_type is TeleoperationEventType.SESSION_STARTED
            and event.outcome is TeleoperationEventOutcome.APPLIED
        ):
            self._sessions_started_total += 1
        elif (
            event.event_type is TeleoperationEventType.SESSION_STOPPED
            and event.outcome is TeleoperationEventOutcome.RELEASED
        ):
            self._sessions_stopped_total += 1
        elif event.event_type is TeleoperationEventType.WATCHDOG_EXPIRED:
            self._watchdog_expirations_total += 1
        elif (
            event.event_type is TeleoperationEventType.SAFETY_RELEASED
            and event.outcome is TeleoperationEventOutcome.RELEASED
        ):
            self._safety_releases_total += 1

        is_command = event.event_type in {
            TeleoperationEventType.FOLLOW_COMMAND,
            TeleoperationEventType.GRIPPER_COMMAND,
        }
        if event.outcome is TeleoperationEventOutcome.FAILED:
            if is_command:
                self._commands_failed_total += 1
            return
        if event.outcome is TeleoperationEventOutcome.SKIPPED:
            if is_command:
                self._commands_skipped_total += 1
            return
        if event.event_type is TeleoperationEventType.FOLLOW_COMMAND:
            self._follow_commands_total += 1
            self._record_command_timing(event)
        elif event.event_type is TeleoperationEventType.GRIPPER_COMMAND:
            self._gripper_commands_total += 1
            self._record_command_timing(event)

    def _record_command_timing(self, event: TeleoperationAuditEvent) -> None:
        duration = event.duration_seconds or 0.0
        self._command_duration_seconds_total += duration
        self._command_duration_seconds_max = max(
            self._command_duration_seconds_max,
            duration,
        )
        completed_at = event.recorded_at_seconds
        if self._first_command_at is None:
            self._first_command_at = completed_at
        if self._last_command_at is not None:
            interval = max(0.0, completed_at - self._last_command_at)
            self._command_interval_seconds_total += interval
            self._command_interval_seconds_max = max(
                self._command_interval_seconds_max,
                interval,
            )
            if self._last_command_interval is not None:
                self._command_interval_jitter_seconds_max = max(
                    self._command_interval_jitter_seconds_max,
                    abs(interval - self._last_command_interval),
                )
            self._last_command_interval = interval
        self._last_command_at = completed_at
        self._observed_commands += 1
