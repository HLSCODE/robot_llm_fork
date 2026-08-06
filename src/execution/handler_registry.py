"""Action-type routing for handlers and their control policies."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import ActionType
from .action_control import ActionControlPolicy, ActionControlPolicyResolver
from .handler_api import (
    ActionExecutionContext,
    ActionHandler,
    ActionHandlerNotFoundError,
    ActionHandlerResult,
    ActionParameters,
)


@dataclass(frozen=True, slots=True)
class _ActionHandlerRegistration:
    handler: ActionHandler
    control_policy: ActionControlPolicyResolver


class ActionHandlerRegistry:
    """The only ActionType-to-handler and control-policy dispatch table."""

    def __init__(self) -> None:
        self._registrations: dict[ActionType, _ActionHandlerRegistration] = {}
        self._frozen = False

    def register(
        self,
        action_type: ActionType,
        handler: ActionHandler,
        control_policy: ActionControlPolicyResolver,
    ) -> None:
        if self._frozen:
            raise RuntimeError("action handler registry is frozen")
        if action_type in self._registrations:
            raise ValueError(f"handler already registered for {action_type.value}")
        self._registrations[action_type] = _ActionHandlerRegistration(
            handler=handler,
            control_policy=control_policy,
        )

    def validate_complete(self) -> None:
        missing = [
            action_type.value
            for action_type in ActionType
            if action_type not in self._registrations
        ]
        if missing:
            raise ActionHandlerNotFoundError(
                "missing action handlers: " + ", ".join(missing)
            )
        self._frozen = True

    def execute(
        self,
        action_type: ActionType,
        parameters: ActionParameters,
        context: ActionExecutionContext,
    ) -> ActionHandlerResult:
        registration = self._registration(action_type)
        context.checkpoint()
        result = registration.handler(parameters, context)
        if not isinstance(result, ActionHandlerResult):
            raise TypeError(
                f"handler for {action_type.value} returned "
                f"{type(result).__name__}, expected ActionHandlerResult"
            )
        context.checkpoint()
        return result

    def control_policy(
        self,
        action_type: ActionType,
        parameters: ActionParameters,
    ) -> ActionControlPolicy:
        policy = self._registration(action_type).control_policy(parameters)
        if not isinstance(policy, ActionControlPolicy):
            raise TypeError(
                f"control policy for {action_type.value} returned "
                f"{type(policy).__name__}, expected ActionControlPolicy"
            )
        return policy

    @property
    def registered_types(self) -> frozenset[ActionType]:
        return frozenset(self._registrations)

    def _registration(self, action_type: ActionType) -> _ActionHandlerRegistration:
        try:
            return self._registrations[action_type]
        except KeyError as exc:
            raise ActionHandlerNotFoundError(
                f"no handler registered for {action_type.value}"
            ) from exc


__all__ = ["ActionHandlerRegistry"]
