"""Thread-safe metrics owned by the application-level vision service."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Lock

from .models import VisionOperation, VisionPipelineResult, VisionResultCode


@dataclass(frozen=True, slots=True)
class VisionMetricsSnapshot:
    model_version: str
    calibration_version: str
    operations_total: int
    operations_succeeded_total: int
    operations_rejected_total: int
    operations_failed_total: int
    capture_operations_total: int
    relocalization_operations_total: int
    frames_processed_total: int
    inference_count_total: int
    operation_duration_seconds_total: float
    operation_duration_seconds_max: float

    def to_dict(self) -> dict[str, object]:
        return {
            "model_version": self.model_version,
            "calibration_version": self.calibration_version,
            "operations_total": self.operations_total,
            "operations_succeeded_total": self.operations_succeeded_total,
            "operations_rejected_total": self.operations_rejected_total,
            "operations_failed_total": self.operations_failed_total,
            "capture_operations_total": self.capture_operations_total,
            "relocalization_operations_total": self.relocalization_operations_total,
            "frames_processed_total": self.frames_processed_total,
            "inference_count_total": self.inference_count_total,
            "operation_duration_seconds_total": self.operation_duration_seconds_total,
            "operation_duration_seconds_max": self.operation_duration_seconds_max,
            "operation_duration_seconds_average": (
                self.operation_duration_seconds_total / self.operations_total
                if self.operations_total
                else 0.0
            ),
            "observed_processing_fps": (
                self.frames_processed_total / self.operation_duration_seconds_total
                if self.operation_duration_seconds_total > 0
                else 0.0
            ),
        }


class VisionMetrics:
    def __init__(self, *, model_version: str, calibration_version: str) -> None:
        self._model_version = model_version
        self._calibration_version = calibration_version
        self._lock = Lock()
        self._operations_total = 0
        self._operations_succeeded_total = 0
        self._operations_rejected_total = 0
        self._operations_failed_total = 0
        self._capture_operations_total = 0
        self._relocalization_operations_total = 0
        self._frames_processed_total = 0
        self._inference_count_total = 0
        self._operation_duration_seconds_total = 0.0
        self._operation_duration_seconds_max = 0.0

    def record_result(
        self,
        operation: VisionOperation,
        result: VisionPipelineResult,
        *,
        duration_seconds: float,
    ) -> None:
        code = (
            VisionResultCode.SUCCEEDED
            if result.successful
            else VisionResultCode.REJECTED
        )
        self._record(
            operation,
            code=code,
            duration_seconds=duration_seconds,
            frames_processed=result.frames_processed,
            inference_count=result.inference_count,
        )

    def record_failure(
        self,
        operation: VisionOperation,
        *,
        duration_seconds: float,
    ) -> None:
        self._record(
            operation,
            code=None,
            duration_seconds=duration_seconds,
            frames_processed=0,
            inference_count=0,
        )

    def snapshot(self) -> VisionMetricsSnapshot:
        with self._lock:
            return VisionMetricsSnapshot(
                model_version=self._model_version,
                calibration_version=self._calibration_version,
                operations_total=self._operations_total,
                operations_succeeded_total=self._operations_succeeded_total,
                operations_rejected_total=self._operations_rejected_total,
                operations_failed_total=self._operations_failed_total,
                capture_operations_total=self._capture_operations_total,
                relocalization_operations_total=self._relocalization_operations_total,
                frames_processed_total=self._frames_processed_total,
                inference_count_total=self._inference_count_total,
                operation_duration_seconds_total=self._operation_duration_seconds_total,
                operation_duration_seconds_max=self._operation_duration_seconds_max,
            )

    def _record(
        self,
        operation: VisionOperation,
        *,
        code: VisionResultCode | None,
        duration_seconds: float,
        frames_processed: int,
        inference_count: int,
    ) -> None:
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("vision metric duration must be finite and non-negative")
        with self._lock:
            self._operations_total += 1
            if code is VisionResultCode.SUCCEEDED:
                self._operations_succeeded_total += 1
            elif code is VisionResultCode.REJECTED:
                self._operations_rejected_total += 1
            else:
                self._operations_failed_total += 1
            if operation is VisionOperation.CAPTURE:
                self._capture_operations_total += 1
            else:
                self._relocalization_operations_total += 1
            self._frames_processed_total += frames_processed
            self._inference_count_total += inference_count
            self._operation_duration_seconds_total += duration_seconds
            self._operation_duration_seconds_max = max(
                self._operation_duration_seconds_max,
                duration_seconds,
            )
