"""Bounded, deterministic per-run export collection, safe for parallel branches."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
import time
from typing import Any

from ..domain.execution_plan import ExecutionStepIdentity
from ..domain.outputs import NodeOutput, RUN_OUTPUT_MAX_BYTES, json_output


class OutputCollector:
    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[str, dict[int, dict[str, Any]]] = {}
        self._modes: dict[str, str] = {}

    def add(
        self, spec: NodeOutput | None, outputs: dict[str, Any], identity: ExecutionStepIdentity
    ) -> None:
        if spec is None:
            return
        try:
            value = spec.select(outputs)
        except (KeyError, IndexError):
            if not spec.required:
                with self._lock:
                    records = self._values.get(spec.name)
                    if spec.mode == "last" and records and max(records) < identity.runtime_index:
                        self._values.pop(spec.name, None)
                return
            raise ValueError(
                f"必需输出 {spec.name} 缺失（字段 {spec.field or '全部结果'}）"
            ) from None
        record = {
            "value": deepcopy(value),
            "source": {**identity.to_event_data(), "timestamp": time.time()},
        }
        with self._lock:
            previous = self._values.get(spec.name)
            records = dict(previous or {})
            records[identity.runtime_index] = record
            if spec.mode == "last":
                records = {max(records): records[max(records)]}
            self._values[spec.name] = records
            self._modes[spec.name] = spec.mode
            try:
                json_output(self._snapshot_unlocked(), limit=RUN_OUTPUT_MAX_BYTES)
            except ValueError:
                if previous is None:
                    self._values.pop(spec.name, None)
                else:
                    self._values[spec.name] = previous
                raise

    def _snapshot_unlocked(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in sorted(self._values):
            records = self._values[name]
            result[name] = (
                [records[index] for index in sorted(records)]
                if self._modes[name] == "all"
                else records[max(records)]["value"]
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._snapshot_unlocked())
