"""Transient execution readiness checks for compiled workflows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from ..domain.execution_plan import ExecutionPlan
from ..domain.models import SequenceEntry
from ..execution import ExecutionSnapshot
from .workflow_compiler import CompiledWorkflow


class ExecutionPreflightPort(Protocol):
    def snapshot(self) -> ExecutionSnapshot: ...

    def required_resources(
        self,
        plan: ExecutionPlan,
    ) -> tuple[str, ...]: ...


class DeviceStatusPort(Protocol):
    def status(self) -> dict[str, dict[str, Any]]: ...


class WorkflowPreflightIssueCode(str, Enum):
    EXECUTION_ACTIVE = "execution_active"
    POLICY_REJECTED = "policy_rejected"
    DEVICE_MISSING = "device_missing"
    DEVICE_NOT_READY = "device_not_ready"


@dataclass(frozen=True, slots=True)
class WorkflowPreflightIssue:
    code: WorkflowPreflightIssueCode
    message: str
    resource_id: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowPreflightResult:
    issues: tuple[WorkflowPreflightIssue, ...]
    required_resources: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.issues


class WorkflowPreflightService:
    """Check transient readiness without reserving resources or starting I/O.

    The execution runtime remains authoritative and rechecks all constraints
    when the compiled sequence is actually submitted.
    """

    def __init__(
        self,
        execution: ExecutionPreflightPort,
        devices: DeviceStatusPort,
    ) -> None:
        self._execution = execution
        self._devices = devices

    def check(self, workflow: CompiledWorkflow) -> WorkflowPreflightResult:
        return self.check_plan(workflow.plan)

    def check_entries(
        self,
        entries: Sequence[SequenceEntry],
    ) -> WorkflowPreflightResult:
        """Check an ad-hoc sequence, including AI-generated commands."""
        return self.check_plan(ExecutionPlan.from_entries(entries))

    def check_plan(self, plan: ExecutionPlan) -> WorkflowPreflightResult:
        issues: list[WorkflowPreflightIssue] = []
        snapshot = self._execution.snapshot()
        if snapshot.active:
            issues.append(
                WorkflowPreflightIssue(
                    WorkflowPreflightIssueCode.EXECUTION_ACTIVE,
                    "已有任务正在执行，当前不能开始新任务",
                )
            )

        try:
            resources = self._execution.required_resources(plan)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            issues.append(
                WorkflowPreflightIssue(
                    WorkflowPreflightIssueCode.POLICY_REJECTED,
                    f"动作控制策略预检失败: {exc}",
                )
            )
            resources = ()

        statuses = self._devices.status()
        for resource_id in resources:
            status = statuses.get(resource_id)
            if status is None:
                issues.append(
                    WorkflowPreflightIssue(
                        WorkflowPreflightIssueCode.DEVICE_MISSING,
                        f"未注册必要设备: {resource_id}",
                        resource_id=resource_id,
                    )
                )
                continue
            if not status.get("available", status.get("ready", False)):
                issues.append(
                    WorkflowPreflightIssue(
                        WorkflowPreflightIssueCode.DEVICE_NOT_READY,
                        f"必要设备未就绪: {resource_id}",
                        resource_id=resource_id,
                    )
                )

        return WorkflowPreflightResult(
            issues=tuple(issues),
            required_resources=resources,
        )
