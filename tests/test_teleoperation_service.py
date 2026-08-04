from __future__ import annotations

import unittest

from src.application import (
    DATA_COLLECTION_TELEOPERATION_OWNER,
    TeleoperationService,
    websocket_teleoperation_owner,
)
from src.application.teleoperation_observability import (
    TeleoperationEventOutcome,
    TeleoperationEventType,
    TeleoperationObservability,
)
from src.core.settings import ApplicationSettings
from src.devices import ArmId, ResourceArbiter
from src.devices.runtime.factory import create_device_runtime
from src.devices.runtime.ids import ROBOT_SYSTEM


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TeleoperationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = create_device_runtime(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        self.resources = ResourceArbiter()
        self.clock = _FakeClock()
        self.audit_events = []
        self.service = TeleoperationService(
            self.runtime,
            self.resources,
            clock=self.clock,
            observability=TeleoperationObservability(self.audit_events.append),
        )

    def tearDown(self) -> None:
        self.service.stop_all()
        self.runtime.shutdown_all()

    def test_multiple_owners_share_one_lease_and_release_only_their_arms(self):
        websocket_owner = websocket_teleoperation_owner("client-1")
        self.service.start(websocket_owner, ("left",))
        self.service.start(
            DATA_COLLECTION_TELEOPERATION_OWNER,
            (ArmId.LEFT, ArmId.RIGHT),
        )

        snapshot = self.service.snapshot()
        self.assertEqual(
            (ArmId.LEFT, ArmId.RIGHT),
            snapshot.active_arms,
        )
        self.assertEqual(
            "teleoperation",
            self.resources.owner_of(ROBOT_SYSTEM),
        )

        self.service.stop(websocket_owner)
        self.assertTrue(self.service.active)
        self.assertIsNotNone(
            self.service.snapshot().owner(
                DATA_COLLECTION_TELEOPERATION_OWNER
            )
        )

        self.service.stop(DATA_COLLECTION_TELEOPERATION_OWNER)
        self.assertFalse(self.service.active)
        self.assertIsNone(self.resources.owner_of(ROBOT_SYSTEM))

    def test_commands_are_owned_counted_and_gripper_is_deduplicated(self):
        owner_id = websocket_teleoperation_owner("client-1")
        self.service.start(owner_id, ("left",))

        command = self.service.follow(
            owner_id,
            "left",
            [0, 1, 2, 3, 4, 5],
            follow=True,
            trajectory_mode=0,
        )
        first_gripper = self.service.set_gripper(owner_id, "left", 500)
        duplicate_gripper = self.service.set_gripper(
            owner_id,
            "left",
            500,
        )

        self.assertEqual(1, command.command_count)
        self.assertTrue(first_gripper.applied)
        self.assertFalse(duplicate_gripper.applied)
        owner = self.service.snapshot().owner(owner_id)
        assert owner is not None
        self.assertEqual(1, owner.command_count("left"))
        self.assertEqual(500, owner.arms[0].last_gripper_position)
        with self.assertRaisesRegex(RuntimeError, "does not control"):
            self.service.follow(
                owner_id,
                "right",
                [0] * 6,
                follow=False,
                trajectory_mode=0,
            )

    def test_watchdog_expires_only_selected_stale_owner_prefix(self):
        websocket_owner = websocket_teleoperation_owner("client-1")
        self.service.start(websocket_owner, ("left",))
        self.service.start(
            DATA_COLLECTION_TELEOPERATION_OWNER,
            ("left", "right"),
        )
        self.clock.now += 1.1

        expired = self.service.expire_stale_owners(
            owner_prefix="websocket:",
            timeout_seconds=1.0,
        )

        self.assertEqual((websocket_owner,), expired)
        self.assertIsNone(self.service.snapshot().owner(websocket_owner))
        self.assertIsNotNone(
            self.service.snapshot().owner(
                DATA_COLLECTION_TELEOPERATION_OWNER
            )
        )
        self.assertIsNotNone(self.resources.owner_of(ROBOT_SYSTEM))

    def test_watchdog_expires_dual_arm_owner_when_one_arm_stream_stalls(self):
        owner_id = websocket_teleoperation_owner("client-1")
        self.service.start(owner_id, ("left", "right"))
        self.clock.now += 0.6
        self.service.follow(
            owner_id,
            "left",
            [0] * 6,
            follow=True,
            trajectory_mode=0,
        )
        self.clock.now += 0.5

        expired = self.service.expire_stale_owners(
            owner_prefix="websocket:",
            timeout_seconds=1.0,
        )

        self.assertEqual((owner_id,), expired)
        self.assertFalse(self.service.active)

    def test_audit_sink_failure_does_not_change_control_result(self):
        def failing_sink(_event):
            raise OSError("audit storage unavailable")

        service = TeleoperationService(
            self.runtime,
            self.resources,
            clock=self.clock,
            observability=TeleoperationObservability(failing_sink),
        )
        owner_id = websocket_teleoperation_owner("sink-failure")

        with self.assertLogs("audit.teleoperation", level="ERROR"):
            service.start(owner_id, ("left",))
            result = service.follow(
                owner_id,
                "left",
                [0] * 6,
                follow=True,
                trajectory_mode=0,
            )
            service.stop(owner_id)

        self.assertEqual(1, result.command_count)
        self.assertFalse(service.active)

    def test_audit_and_metrics_cover_commands_skips_failures_and_watchdog(self):
        owner_id = websocket_teleoperation_owner("audit-client")
        self.service.start(owner_id, ("left",))
        self.clock.now += 0.01
        self.service.follow(
            owner_id,
            "left",
            [0] * 6,
            follow=True,
            trajectory_mode=0,
        )
        self.clock.now += 0.02
        self.service.set_gripper(owner_id, "left", 500)
        self.service.set_gripper(owner_id, "left", 500)
        self.clock.now += 0.03
        self.service.follow(
            owner_id,
            "left",
            [1] * 6,
            follow=True,
            trajectory_mode=0,
        )
        with self.assertRaisesRegex(RuntimeError, "does not control"):
            self.service.follow(
                owner_id,
                "right",
                [0] * 6,
                follow=False,
                trajectory_mode=0,
            )
        self.clock.now += 1.1
        self.service.expire_stale_owners(
            owner_prefix="websocket:",
            timeout_seconds=1.0,
        )

        snapshot = self.service.metrics_snapshot()
        self.assertEqual(1, snapshot.sessions_started_total)
        self.assertEqual(2, snapshot.follow_commands_total)
        self.assertEqual(1, snapshot.gripper_commands_total)
        self.assertEqual(1, snapshot.commands_skipped_total)
        self.assertEqual(1, snapshot.commands_failed_total)
        self.assertEqual(1, snapshot.watchdog_expirations_total)
        self.assertAlmostEqual(40.0, snapshot.observed_throughput_hz)
        self.assertAlmostEqual(0.01, snapshot.command_interval_jitter_seconds_max)
        self.assertEqual(
            [
                TeleoperationEventType.SESSION_STARTED,
                TeleoperationEventType.FOLLOW_COMMAND,
                TeleoperationEventType.GRIPPER_COMMAND,
                TeleoperationEventType.GRIPPER_COMMAND,
                TeleoperationEventType.FOLLOW_COMMAND,
                TeleoperationEventType.FOLLOW_COMMAND,
                TeleoperationEventType.WATCHDOG_EXPIRED,
            ],
            [event.event_type for event in self.audit_events],
        )
        self.assertEqual(
            TeleoperationEventOutcome.FAILED,
            self.audit_events[-2].outcome,
        )
        serialized = str([event.to_dict() for event in self.audit_events])
        self.assertNotIn("joints", serialized)
        self.assertNotIn("500", serialized)


if __name__ == "__main__":
    unittest.main()
