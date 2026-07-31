from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from ..device_runtime import StopMode
from ..device_runtime.ids import (
    BODY_AXIS,
    CAMERA,
    EXPRESSION_DISPLAY,
    MOBILE_BASE,
    PIPETTE,
    POWDER_DISPENSER,
    RELAY_BANK,
    ROBOT_SYSTEM,
    TOOL_CHANGER,
)


ActionParameters = Mapping[str, Any]
COOPERATIVE_CANCEL_MAX_LATENCY_SECONDS = 0.1


class ActionCancellationMode(str, Enum):
    """How an action can observe or assist a cancellation request."""

    BOUNDED_COOPERATIVE = "bounded_cooperative"
    AFTER_BLOCKING_CALL = "after_blocking_call"
    DEVICE_ASSISTED = "device_assisted"


@dataclass(frozen=True, slots=True)
class ActionStopTarget:
    """One device that must support out-of-band stopping for an action."""

    device_id: str
    required_modes: frozenset[StopMode]

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("stop target device_id must not be empty")
        if not isinstance(self.required_modes, frozenset):
            raise TypeError("stop target required_modes must be a frozenset")
        if not self.required_modes:
            raise ValueError("stop target must declare at least one mode")
        if not all(
            isinstance(mode, StopMode) for mode in self.required_modes
        ):
            raise TypeError("stop target modes must be StopMode values")
        unsupported = self.required_modes - {
            StopMode.QUICK,
            StopMode.EMERGENCY,
        }
        if unsupported:
            values = ", ".join(sorted(mode.value for mode in unsupported))
            raise ValueError(f"invalid device stop modes: {values}")

    def to_event_data(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "required_modes": [
                mode.value
                for mode in sorted(
                    self.required_modes,
                    key=lambda item: item.value,
                )
            ],
        }


@dataclass(frozen=True, slots=True)
class ActionControlPolicy:
    """Auditable cancellation contract for one resolved action path."""

    operation: str
    cancellation_mode: ActionCancellationMode
    blocking_device_call: bool
    device_ids: tuple[str, ...] = ()
    stop_targets: tuple[ActionStopTarget, ...] = ()
    expected_max_cancel_latency_seconds: float | None = None
    hardware_validation_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("control policy operation must not be empty")
        if not isinstance(self.device_ids, tuple):
            raise TypeError("control policy device_ids must be a tuple")
        if not isinstance(self.stop_targets, tuple):
            raise TypeError("control policy stop_targets must be a tuple")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("control policy device_ids must be unique")
        if any(
            not isinstance(device_id, str) or not device_id.strip()
            for device_id in self.device_ids
        ):
            raise ValueError("control policy device_ids must not be empty")

        stop_device_ids = tuple(
            target.device_id for target in self.stop_targets
        )
        if len(set(stop_device_ids)) != len(stop_device_ids):
            raise ValueError("control policy stop targets must be unique")
        unknown_targets = set(stop_device_ids) - set(self.device_ids)
        if unknown_targets:
            values = ", ".join(sorted(unknown_targets))
            raise ValueError(
                f"stop targets are missing from device_ids: {values}"
            )

        latency = self.expected_max_cancel_latency_seconds
        if latency is not None and latency <= 0:
            raise ValueError(
                "expected max cancel latency must be positive"
            )

        if (
            self.cancellation_mode
            is ActionCancellationMode.BOUNDED_COOPERATIVE
        ):
            if self.blocking_device_call:
                raise ValueError(
                    "bounded cooperative policy cannot contain "
                    "a blocking device call"
                )
            if latency is None:
                raise ValueError(
                    "bounded cooperative policy must declare cancel latency"
                )
            if self.stop_targets:
                raise ValueError(
                    "bounded cooperative policy cannot have stop targets"
                )
            if self.hardware_validation_required:
                raise ValueError(
                    "bounded cooperative policy cannot require "
                    "hardware validation"
                )
            return

        if not self.blocking_device_call:
            raise ValueError(
                "hardware cancellation policy must contain "
                "a blocking device call"
            )
        if latency is not None:
            raise ValueError(
                "unvalidated hardware policy cannot claim cancel latency"
            )

        if (
            self.cancellation_mode
            is ActionCancellationMode.AFTER_BLOCKING_CALL
        ):
            if self.stop_targets:
                raise ValueError(
                    "after-blocking-call policy cannot have stop targets"
                )
            if self.hardware_validation_required:
                raise ValueError(
                    "after-blocking-call policy cannot require "
                    "stop hardware validation"
                )
            return

        if not self.stop_targets:
            raise ValueError(
                "device-assisted policy must declare stop targets"
            )
        if not self.hardware_validation_required:
            raise ValueError(
                "device-assisted policy must require hardware validation"
            )

    def to_event_data(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "cancellation_mode": self.cancellation_mode.value,
            "blocking_device_call": self.blocking_device_call,
            "device_ids": list(self.device_ids),
            "stop_targets": [
                target.to_event_data() for target in self.stop_targets
            ],
            "expected_max_cancel_latency_seconds": (
                self.expected_max_cancel_latency_seconds
            ),
            "hardware_validation_required": (
                self.hardware_validation_required
            ),
        }


ActionControlPolicyResolver = Callable[
    [ActionParameters],
    ActionControlPolicy,
]


def validate_control_policy_routes(
    route_group: str,
    registered_routes: frozenset[str],
    policies: Mapping[str, ActionControlPolicy],
) -> None:
    """Fail application composition when behavior and policy routes diverge."""
    policy_routes = frozenset(policies)
    missing_policies = registered_routes - policy_routes
    orphaned_policies = policy_routes - registered_routes
    if not missing_policies and not orphaned_policies:
        return

    details: list[str] = []
    if missing_policies:
        details.append(
            "missing policies: " + ", ".join(sorted(missing_policies))
        )
    if orphaned_policies:
        details.append(
            "orphaned policies: " + ", ".join(sorted(orphaned_policies))
        )
    raise ValueError(
        f"{route_group} control policy routes mismatch; "
        + "; ".join(details)
    )


def resolve_wait_control_policy(
    _parameters: ActionParameters,
) -> ActionControlPolicy:
    return _cooperative_policy("wait")


def resolve_inspect_control_policy(
    _parameters: ActionParameters,
) -> ActionControlPolicy:
    return _cooperative_policy("inspect")


def resolve_move_control_policy(
    parameters: ActionParameters,
) -> ActionControlPolicy:
    target = str(parameters.get("目标", "机械臂")).strip()
    return MOVE_CONTROL_POLICIES.get(
        target,
        _cooperative_policy("move.route"),
    )


def resolve_base_move_control_policy(
    parameters: ActionParameters,
) -> ActionControlPolicy:
    move_mode = str(parameters.get("move_mode", "position")).strip()
    return BASE_MOVE_CONTROL_POLICIES.get(
        move_mode,
        _cooperative_policy("mobile_base.route"),
    )


def resolve_manipulate_control_policy(
    parameters: ActionParameters,
) -> ActionControlPolicy:
    executor = str(parameters.get("执行器", "快换手")).strip()
    return MANIPULATE_CONTROL_POLICIES.get(
        executor,
        _cooperative_policy("manipulate.route"),
    )


def resolve_change_tool_control_policy(
    parameters: ActionParameters,
) -> ActionControlPolicy:
    additional_devices = (
        (PIPETTE,)
        if str(parameters.get("Operation", "取")).strip() == "放"
        else ()
    )
    return _robot_stoppable_policy(
        "tool_rack.change_tool",
        *additional_devices,
    )


def resolve_vision_capture_control_policy(
    _parameters: ActionParameters,
) -> ActionControlPolicy:
    return _robot_stoppable_policy("vision.capture", CAMERA)


def resolve_vision_relocalization_control_policy(
    _parameters: ActionParameters,
) -> ActionControlPolicy:
    return _robot_stoppable_policy("vision.relocalize", CAMERA)


def resolve_trajectory_control_policy(
    _parameters: ActionParameters,
) -> ActionControlPolicy:
    return _robot_stoppable_policy("trajectory.send")


def _cooperative_policy(operation: str) -> ActionControlPolicy:
    return ActionControlPolicy(
        operation=operation,
        cancellation_mode=ActionCancellationMode.BOUNDED_COOPERATIVE,
        blocking_device_call=False,
        expected_max_cancel_latency_seconds=(
            COOPERATIVE_CANCEL_MAX_LATENCY_SECONDS
        ),
    )


def _blocking_policy(
    operation: str,
    *device_ids: str,
) -> ActionControlPolicy:
    return ActionControlPolicy(
        operation=operation,
        cancellation_mode=ActionCancellationMode.AFTER_BLOCKING_CALL,
        blocking_device_call=True,
        device_ids=device_ids,
    )


def _robot_stoppable_policy(
    operation: str,
    *additional_device_ids: str,
) -> ActionControlPolicy:
    return ActionControlPolicy(
        operation=operation,
        cancellation_mode=ActionCancellationMode.DEVICE_ASSISTED,
        blocking_device_call=True,
        device_ids=(ROBOT_SYSTEM, *additional_device_ids),
        stop_targets=(
            ActionStopTarget(
                device_id=ROBOT_SYSTEM,
                required_modes=frozenset(
                    {StopMode.QUICK, StopMode.EMERGENCY}
                ),
            ),
        ),
        hardware_validation_required=True,
    )


MOVE_CONTROL_POLICIES: Mapping[str, ActionControlPolicy] = MappingProxyType({
    "机械臂": _robot_stoppable_policy("robot_system.move_to_pose"),
    "身体": _blocking_policy("body_axis.move_to", BODY_AXIS),
})

BASE_MOVE_CONTROL_POLICIES: Mapping[
    str,
    ActionControlPolicy,
] = MappingProxyType({
    "position": _blocking_policy(
        "mobile_base.move_to_position",
        MOBILE_BASE,
    ),
    "distance": _blocking_policy(
        "mobile_base.move_slowly",
        MOBILE_BASE,
    ),
})

_EXPRESSION_CONTROL_POLICY = _blocking_policy(
    "expression_display.execute",
    EXPRESSION_DISPLAY,
)
MANIPULATE_CONTROL_POLICIES: Mapping[
    str,
    ActionControlPolicy,
] = MappingProxyType({
    "快换手": _blocking_policy(
        "tool_changer.set_locked",
        TOOL_CHANGER,
    ),
    "继电器": _blocking_policy("relay.set_channel", RELAY_BANK),
    "夹爪": _blocking_policy("gripper.execute", ROBOT_SYSTEM),
    "吸液枪": _blocking_policy("pipette.execute", PIPETTE),
    "表情屏": _EXPRESSION_CONTROL_POLICY,
    "表情": _EXPRESSION_CONTROL_POLICY,
    "expression_display": _EXPRESSION_CONTROL_POLICY,
    "expression": _EXPRESSION_CONTROL_POLICY,
    "右臂转圈注液": _robot_stoppable_policy(
        "circle_dispense.execute",
        PIPETTE,
    ),
    "智能加粉": _blocking_policy(
        "powder_dispense.run",
        POWDER_DISPENSER,
    ),
    "加粉装置": _blocking_policy(
        "powder_dispenser.execute",
        POWDER_DISPENSER,
    ),
})
