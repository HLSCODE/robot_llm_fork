from __future__ import annotations

import unittest

from src.application import create_application_services
from src.core.models import ActionDefinition, ActionType, SequenceItem
from src.core.settings import ApplicationSettings
from src.device_runtime import (
    ArmId,
    DeviceCapability,
    DeviceRegistration,
    DeviceRuntime,
    DeviceState,
)
from src.device_runtime.ids import (
    EXPRESSION_DISPLAY,
    NECK,
    PIPETTE,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
    TOOL_CHANGER,
)
from src.execution import (
    ActionCancelledError,
    ActionExecutionContext,
    ActionHandlerResult,
    ActionResultCode,
    ExecutionControl,
    ExecutionState,
)
from src.execution.handlers import (
    ExpressionDisplayActionHandler,
    GripperActionHandler,
    ManipulateActionHandler,
    ManipulationHandlerOptions,
    NeckActionHandler,
    PipetteActionHandler,
    PowderDispenseActionHandler,
    RelayActionHandler,
    TappingActionHandler,
    ToolChangerActionHandler,
)


class _ToolChanger:
    def __init__(self) -> None:
        self.locked: bool | None = None

    def set_locked(self, locked: bool) -> None:
        self.locked = locked

    def close(self) -> None:
        return None


class _Relay:
    def __init__(self) -> None:
        self.channels: list[tuple[int, bool]] = []

    def set_channel(self, channel: int, enabled: bool) -> None:
        self.channels.append((channel, enabled))

    def close(self) -> None:
        return None


class _Gripper:
    def __init__(self, failures_before_success: int = 0) -> None:
        self.failures_before_success = failures_before_success
        self.calls: list[tuple[str, ArmId]] = []

    def open_gripper(self, arm: ArmId) -> None:
        self._record("open", arm)

    def close_gripper(self, arm: ArmId) -> None:
        self._record("close", arm)

    def move_gripper(self, arm: ArmId, _position: int) -> None:
        self._record("move", arm)

    def _record(self, operation: str, arm: ArmId) -> None:
        self.calls.append((operation, arm))
        if len(self.calls) <= self.failures_before_success:
            raise RuntimeError("transient gripper failure")


class _Pipette:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def initialize(self) -> bool:
        return True

    def set_absorb_speed(self, speed_ul_s: int) -> bool:
        self.calls.append(("absorb_speed", speed_ul_s))
        return True

    def set_dispense_speed(self, speed_ul_s: int) -> bool:
        self.calls.append(("dispense_speed", speed_ul_s))
        return True

    def absorb(self, volume_ul: int) -> bool:
        self.calls.append(("absorb", volume_ul))
        return True

    def dispense(self, volume_ul: int) -> bool:
        self.calls.append(("dispense", volume_ul))
        return True

    def dispense_all(self) -> bool:
        self.calls.append(("dispense_all", None))
        return True

    def eject_tip(self) -> bool:
        self.calls.append(("eject_tip", None))
        return True

    def close(self) -> None:
        return None


class _Neck:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, int | None, int | None]] = []

    def move_horizontal(self, pwm: int, time_ms: int | None = None) -> None:
        self.calls.append(("horizontal", pwm, None, time_ms))

    def move_vertical(self, pwm: int, time_ms: int | None = None) -> None:
        self.calls.append(("vertical", None, pwm, time_ms))

    def move_both(
        self,
        horizontal_pwm: int,
        vertical_pwm: int,
        time_ms: int | None = None,
    ) -> None:
        self.calls.append(("both", horizontal_pwm, vertical_pwm, time_ms))

    def reset(self, time_ms: int | None = None) -> None:
        self.calls.append(("reset", None, None, time_ms))

    def close(self) -> None:
        return None


class _ExpressionDisplay:
    def __init__(self) -> None:
        self.expressions: list[str | int] = []
        self.closed = False

    def switch(self, expression: str | int) -> str | int:
        self.expressions.append(expression)
        return expression

    def close(self) -> None:
        self.closed = True


class _PowderDispenser:
    def __init__(
        self,
        *,
        cancel_control: ExecutionControl | None = None,
    ) -> None:
        self.calls: list[tuple[str, int | None]] = []
        self._cancel_control = cancel_control

    def enable_all(self) -> None:
        self.calls.append(("enable", None))

    def gripper_move_to(self, percent: int) -> None:
        self.calls.append(("gripper_move", percent))

    def gripper_grip(self) -> None:
        self.calls.append(("gripper_grip", None))

    def gripper_release(self) -> None:
        self.calls.append(("gripper_release", None))

    def lift_up(self, steps: int) -> None:
        self.calls.append(("lift_up", steps))

    def lift_down(self, steps: int) -> None:
        self.calls.append(("lift_down", steps))

    def lift_stop(self) -> None:
        self.calls.append(("lift_stop", None))

    def lift_to_dispense(self, position: int) -> None:
        self.calls.append(("lift_dispense", position))

    def lift_to_safe(self, position: int) -> None:
        self.calls.append(("lift_safe", position))

    def rotation_cw(self, steps: int) -> None:
        self.calls.append(("rotation_cw", steps))

    def rotation_ccw(self, steps: int) -> None:
        self.calls.append(("rotation_ccw", steps))

    def rotation_stop(self) -> None:
        self.calls.append(("rotation_stop", None))

    def rotation_move_relative(self, delta_steps: int) -> None:
        self.calls.append(("rotation_move", delta_steps))
        if self._cancel_control is not None:
            self._cancel_control.cancel()

    def rotation_to_home(self, position: int) -> None:
        self.calls.append(("rotation_home", position))

    def close(self) -> None:
        return None


def _runtime_with(
    device_id: str,
    capability: DeviceCapability,
    device,
) -> DeviceRuntime:
    runtime = DeviceRuntime()
    runtime.register(
        DeviceRegistration(
            device_id=device_id,
            capabilities=frozenset({capability}),
            factory=lambda: device,
            close=lambda value: value.close() if hasattr(value, "close") else None,
        )
    )
    return runtime


def _context(
    control: ExecutionControl | None = None,
) -> tuple[ActionExecutionContext, list[tuple[str, str]]]:
    logs: list[tuple[str, str]] = []
    return (
        ActionExecutionContext(
            action_name="manipulation test",
            control=control or ExecutionControl(),
            timeout_seconds=1.0,
            log=lambda message, level: logs.append((message, level)),
        ),
        logs,
    )


class DiscreteOutputHandlerTests(unittest.TestCase):
    def test_tool_changer_and_relay_use_capability_contracts(self):
        tool = _ToolChanger()
        tool_runtime = _runtime_with(
            TOOL_CHANGER,
            DeviceCapability.TOOL_CHANGER,
            tool,
        )
        relay = _Relay()
        relay_runtime = _runtime_with(
            RELAY_BANK,
            DeviceCapability.DIGITAL_OUTPUT,
            relay,
        )
        context, _logs = _context()

        self.assertTrue(
            ToolChangerActionHandler(tool_runtime)(
                {"操作": "开"},
                context,
            ).successful
        )
        self.assertTrue(
            RelayActionHandler(relay_runtime)(
                {"编号": "2", "操作": "关"},
                context,
            ).successful
        )

        self.assertFalse(tool.locked)
        self.assertEqual([(2, False)], relay.channels)

    def test_unknown_executor_is_rejected_by_nested_registry(self):
        context, logs = _context()
        handler = ManipulateActionHandler(
            {"known": lambda _parameters, _context: ActionHandlerResult.succeeded()}
        )

        result = handler({"执行器": "unknown"}, context)
        self.assertFalse(result.successful)
        self.assertEqual(
            ActionResultCode.UNSUPPORTED_OPERATION,
            result.code,
        )
        self.assertEqual("manipulate.route", result.operation)
        self.assertEqual(("未知的执行器: unknown", "error"), logs[-1])


class GripperActionHandlerTests(unittest.TestCase):
    def test_gripper_retry_policy_is_bounded_and_configurable(self):
        gripper = _Gripper(failures_before_success=2)
        runtime = _runtime_with(
            ROBOT_SYSTEM,
            DeviceCapability.GRIPPER,
            gripper,
        )
        handler = GripperActionHandler(
            runtime,
            ManipulationHandlerOptions(
                gripper_max_attempts=3,
                gripper_retry_delay_seconds=0,
            ),
        )
        context, _logs = _context()

        self.assertTrue(handler({"操作": "关"}, context).successful)
        self.assertEqual(
            [("close", ArmId.LEFT)] * 3,
            gripper.calls,
        )


class PipetteActionHandlerTests(unittest.TestCase):
    def test_pipette_normalizes_speed_volume_and_false_string(self):
        pipette = _Pipette()
        runtime = _runtime_with(
            PIPETTE,
            DeviceCapability.PIPETTE,
            pipette,
        )
        handler = PipetteActionHandler(runtime)
        context, _logs = _context()

        self.assertTrue(
            handler(
                {
                    "操作": "吐",
                    "容量": "25",
                    "吐液速度": "10",
                    "全吐": "false",
                },
                context,
            ).successful
        )

        self.assertEqual(
            [("dispense_speed", 10), ("dispense", 25)],
            pipette.calls,
        )

    def test_pipette_rejects_non_positive_capacity(self):
        pipette = _Pipette()
        runtime = _runtime_with(
            PIPETTE,
            DeviceCapability.PIPETTE,
            pipette,
        )
        context, logs = _context()

        self.assertFalse(
            PipetteActionHandler(runtime)(
                {"操作": "吸", "容量": 0},
                context,
            ).successful
        )
        self.assertEqual([], pipette.calls)
        self.assertEqual(
            DeviceState.REGISTERED,
            runtime.snapshot(PIPETTE).state,
        )
        self.assertEqual("error", logs[-1][1])


class NeckActionHandlerTests(unittest.TestCase):
    def test_neck_moves_both_axes_with_normalized_parameters(self):
        neck = _Neck()
        runtime = _runtime_with(NECK, DeviceCapability.NECK_MOTION, neck)
        context, _logs = _context()

        result = NeckActionHandler(runtime)(
            {
                "操作": "双轴移动",
                "水平PWM": "1700",
                "垂直PWM": "1500",
                "时长ms": "250",
            },
            context,
        )

        self.assertTrue(result.successful)
        self.assertEqual([("both", 1700, 1500, 250)], neck.calls)

    def test_neck_reset_uses_unified_runtime(self):
        neck = _Neck()
        runtime = _runtime_with(NECK, DeviceCapability.NECK_MOTION, neck)
        context, _logs = _context()

        result = NeckActionHandler(runtime)(
            {"操作": "复位", "时长ms": 0},
            context,
        )

        self.assertTrue(result.successful)
        self.assertEqual([("reset", None, None, 0)], neck.calls)

    def test_neck_rejects_invalid_parameters_before_device_initialization(self):
        neck = _Neck()
        runtime = _runtime_with(NECK, DeviceCapability.NECK_MOTION, neck)
        context, logs = _context()

        result = NeckActionHandler(runtime)(
            {"操作": "水平移动", "水平PWM": 3000},
            context,
        )

        self.assertFalse(result.successful)
        self.assertIs(ActionResultCode.INVALID_PARAMETERS, result.code)
        self.assertEqual([], neck.calls)
        self.assertEqual(DeviceState.REGISTERED, runtime.snapshot(NECK).state)
        self.assertEqual("error", logs[-1][1])


class DisplayAndPowderHandlerTests(unittest.TestCase):
    def test_expression_alias_and_runtime_owned_shutdown(self):
        display = _ExpressionDisplay()
        runtime = _runtime_with(
            EXPRESSION_DISPLAY,
            DeviceCapability.EXPRESSION_DISPLAY,
            display,
        )
        handler = ExpressionDisplayActionHandler(runtime)
        context, _logs = _context()

        self.assertTrue(handler({"expression": "happy"}, context).successful)
        self.assertTrue(handler({"操作": "close"}, context).successful)

        self.assertEqual(["happy"], display.expressions)
        self.assertTrue(display.closed)

    def test_tapping_operation_is_resolved_before_device_call(self):
        powder = _PowderDispenser()
        runtime = _runtime_with(
            POWDER_DISPENSER,
            DeviceCapability.POWDER_DISPENSER,
            powder,
        )
        context, _logs = _context()

        self.assertTrue(
            TappingActionHandler(runtime)(
                {"操作": "针上升", "步数": "12"},
                context,
            ).successful
        )
        self.assertEqual(
            [("enable", None), ("lift_up", 12)],
            powder.calls,
        )

    def test_invalid_tapping_input_does_not_initialize_device(self):
        powder = _PowderDispenser()
        runtime = _runtime_with(
            POWDER_DISPENSER,
            DeviceCapability.POWDER_DISPENSER,
            powder,
        )
        context, _logs = _context()
        handler = TappingActionHandler(runtime)

        self.assertFalse(
            handler(
                {"操作": "针上升", "步数": 0},
                context,
            ).successful
        )
        self.assertEqual([], powder.calls)
        self.assertEqual(
            DeviceState.REGISTERED,
            runtime.snapshot(POWDER_DISPENSER).state,
        )

    def test_powder_cancellation_still_runs_safe_return(self):
        control = ExecutionControl()
        powder = _PowderDispenser(cancel_control=control)
        runtime = _runtime_with(
            POWDER_DISPENSER,
            DeviceCapability.POWDER_DISPENSER,
            powder,
        )
        context, _logs = _context(control)
        handler = PowderDispenseActionHandler(
            runtime,
            lambda: {
                "lift_safe_position": 1,
                "lift_dispense_position": 2,
                "rotation_home_position": 3,
            },
            read_balance=lambda: 1.0,
        )

        with self.assertRaises(ActionCancelledError):
            handler(
                {
                    "target_mg": 100,
                    "tolerance_mg": 1,
                    "settle_seconds": 10,
                },
                context,
            )

        self.assertIn(("rotation_stop", None), powder.calls)
        self.assertIn(("lift_safe", 1), powder.calls)
        self.assertIn(("rotation_home", 3), powder.calls)

    def test_powder_max_rounds_maps_to_target_not_reached(self):
        powder = _PowderDispenser()
        runtime = _runtime_with(
            POWDER_DISPENSER,
            DeviceCapability.POWDER_DISPENSER,
            powder,
        )
        readings = iter((1.0, 1.001))
        context, _logs = _context()
        handler = PowderDispenseActionHandler(
            runtime,
            lambda: {},
            read_balance=lambda: next(readings),
        )

        result = handler(
            {
                "target_mg": 100,
                "tolerance_mg": 1,
                "max_rounds": 1,
                "settle_seconds": 0,
            },
            context,
        )

        self.assertFalse(result.successful)
        self.assertIs(ActionResultCode.TARGET_NOT_REACHED, result.code)
        self.assertIn("未达到目标", result.message)


class ManipulationRuntimeIntegrationTests(unittest.TestCase):
    def test_discrete_manipulation_actions_use_unified_registry(self):
        services = create_application_services(
            ApplicationSettings.defaults(),
            simulation=True,
        )
        definitions = (
            ("tool", {"执行器": "快换手", "操作": "关"}),
            ("relay", {"执行器": "继电器", "编号": 1, "操作": "开"}),
            ("gripper", {"执行器": "夹爪", "操作": "开"}),
            ("pipette", {"执行器": "吸液枪", "操作": "吸", "容量": 10}),
            (
                "neck",
                {
                    "执行器": "颈部",
                    "操作": "双轴移动",
                    "水平PWM": 1600,
                    "垂直PWM": 1600,
                    "时长ms": 0,
                },
            ),
            (
                "display",
                {"执行器": "expression", "expression": "happy"},
            ),
            (
                "tapping",
                {"执行器": "加粉装置", "操作": "针停止"},
            ),
        )
        sequence = [
            SequenceItem.from_definition(
                ActionDefinition(
                    id=action_id,
                    name=action_id,
                    type=ActionType.MANIPULATE,
                    parameters=parameters,
                )
            )
            for action_id, parameters in definitions
        ]

        final = services.execution.start(
            sequence,
            origin="test",
        ).wait(1)

        self.assertEqual(ExecutionState.SUCCEEDED, final.state)
        self.assertFalse(services.devices.shutdown_all())


if __name__ == "__main__":
    unittest.main()
