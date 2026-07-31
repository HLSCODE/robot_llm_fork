from __future__ import annotations

import unittest

from src.application import (
    DATA_COLLECTION_TELEOPERATION_OWNER,
    TeleoperationService,
    websocket_teleoperation_owner,
)
from src.core.settings import ApplicationSettings
from src.device_runtime import ArmId, ResourceArbiter
from src.device_runtime.factory import create_device_runtime
from src.device_runtime.ids import ROBOT_SYSTEM


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
        self.service = TeleoperationService(
            self.runtime,
            self.resources,
            clock=self.clock,
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


if __name__ == "__main__":
    unittest.main()
