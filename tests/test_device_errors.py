from __future__ import annotations

import unittest

from src.device_control_sdk import (
    ProtocolError,
    TransportError,
    TransportErrorCategory,
)
from src.device_runtime import (
    ArmId,
    DeviceErrorCategory,
    DeviceInitializationError,
    RobotOperationError,
)
from src.device_runtime.errors import normalize_device_error
from src.execution import (
    ActionExecutionContext,
    ActionResultCode,
    ExecutionControl,
)


class DeviceErrorMappingTests(unittest.TestCase):
    def test_transport_timeout_has_stable_safe_semantics(self) -> None:
        error = TransportError(
            "read COM9 timed out with internal details",
            category=TransportErrorCategory.TIMEOUT,
            port="COM9",
            operation="read",
        )

        normalized = normalize_device_error(
            error,
            device_id="pipette",
            operation="pipette.absorb",
        )

        self.assertEqual(DeviceErrorCategory.TIMEOUT, normalized.category)
        self.assertEqual("timeout", normalized.raw_error_code)
        self.assertEqual(
            "设备响应超时（设备=pipette，操作=pipette.absorb）",
            normalized.user_message,
        )
        self.assertNotIn("COM9", normalized.user_message)
        self.assertIn("COM9", normalized.diagnostic_message)

    def test_protocol_and_initialization_failures_are_distinct(self) -> None:
        protocol = normalize_device_error(
            ProtocolError("bad CRC"),
            device_id="relay-bank",
            operation="relay.set_channel",
        )
        unavailable = normalize_device_error(
            DeviceInitializationError("SDK missing"),
            device_id="robot-system",
            operation="device.initialize",
        )

        self.assertEqual(DeviceErrorCategory.PROTOCOL, protocol.category)
        self.assertEqual(
            DeviceErrorCategory.UNAVAILABLE,
            unavailable.category,
        )

    def test_robot_rejection_preserves_vendor_code_only(self) -> None:
        normalized = normalize_device_error(
            RobotOperationError(
                "move_to_pose",
                ArmId.LEFT,
                code=17,
                detail="vendor diagnostic",
            ),
            device_id="robot-system",
            operation="robot_system.move_to_pose",
        )

        self.assertEqual(DeviceErrorCategory.REJECTED, normalized.category)
        self.assertEqual("17", normalized.raw_error_code)
        self.assertNotIn("vendor diagnostic", normalized.user_message)

    def test_action_failure_exposes_category_without_internal_detail(self) -> None:
        logs: list[tuple[str, str]] = []
        context = ActionExecutionContext(
            action_name="pipette",
            control=ExecutionControl(),
            timeout_seconds=1,
            log=lambda message, level: logs.append((message, level)),
        )

        result = context.failure(
            ActionResultCode.DEVICE_OPERATION_FAILED,
            "legacy detailed message",
            operation="pipette.absorb",
            device_id="pipette",
            error=TransportError(
                "secret driver detail",
                category=TransportErrorCategory.TIMEOUT,
            ),
        )

        self.assertEqual("timeout", result.error_category)
        self.assertEqual("timeout", result.raw_error_code)
        self.assertNotIn("secret driver detail", result.message)
        self.assertEqual((result.message, "error"), logs[-1])

    def test_robot_rejection_maps_to_action_rejected_code(self) -> None:
        context = ActionExecutionContext(
            action_name="move",
            control=ExecutionControl(),
            timeout_seconds=1,
            log=lambda _message, _level: None,
        )

        result = context.failure(
            ActionResultCode.DEVICE_OPERATION_FAILED,
            "move failed",
            operation="robot_system.move_to_pose",
            device_id="robot-system",
            error=RobotOperationError(
                "move_to_pose",
                ArmId.LEFT,
                code=9,
            ),
        )

        self.assertEqual(ActionResultCode.OPERATION_REJECTED, result.code)
        self.assertEqual("rejected", result.error_category)
        self.assertEqual("9", result.raw_error_code)


if __name__ == "__main__":
    unittest.main()
