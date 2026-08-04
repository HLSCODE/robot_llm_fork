from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class DepthCameraFrame:
    """One RGB/depth frameset with device and host timing metadata."""

    camera_name: str
    camera_serial: str
    color_bgr: np.ndarray
    depth_uint16: np.ndarray
    intrinsics: np.ndarray
    distortion_coefficients: np.ndarray
    distortion_model: str
    depth_scale_metres: float
    color_hardware_timestamp_ms: float
    depth_hardware_timestamp_ms: float
    color_frame_number: int
    depth_frame_number: int
    hardware_timestamp_domain: str
    received_at_utc_ns: int
    received_at_monotonic_ns: int
    depth_aligned_to_color: bool

    def __post_init__(self) -> None:
        if not self.camera_name.strip():
            raise ValueError("camera_name must not be empty")
        if self.color_bgr.ndim != 3 or self.color_bgr.shape[2] != 3:
            raise ValueError("color_bgr must have shape HxWx3")
        if self.color_bgr.dtype != np.uint8:
            raise ValueError("color_bgr must use uint8")
        if self.depth_uint16.ndim != 2:
            raise ValueError("depth_uint16 must have shape HxW")
        if self.depth_uint16.dtype != np.uint16:
            raise ValueError("depth_uint16 must use uint16")
        if (
            self.depth_aligned_to_color
            and self.color_bgr.shape[:2] != self.depth_uint16.shape
        ):
            raise ValueError("aligned color and depth image dimensions must match")
        if self.intrinsics.shape != (3, 3):
            raise ValueError("intrinsics must have shape 3x3")
        if not np.all(np.isfinite(self.intrinsics)):
            raise ValueError("intrinsics must contain finite values")
        if self.intrinsics[0, 0] <= 0 or self.intrinsics[1, 1] <= 0:
            raise ValueError("camera focal lengths must be positive")
        if self.distortion_coefficients.ndim != 1:
            raise ValueError("distortion_coefficients must be one-dimensional")
        if not np.all(np.isfinite(self.distortion_coefficients)):
            raise ValueError("distortion coefficients must be finite")
        if not self.distortion_model.strip():
            raise ValueError("distortion_model must not be empty")
        if not math.isfinite(self.depth_scale_metres) or self.depth_scale_metres <= 0:
            raise ValueError("depth_scale_metres must be positive and finite")
        for name, value in (
            ("color_hardware_timestamp_ms", self.color_hardware_timestamp_ms),
            ("depth_hardware_timestamp_ms", self.depth_hardware_timestamp_ms),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.color_frame_number < 0 or self.depth_frame_number < 0:
            raise ValueError("camera frame numbers must not be negative")
        if not self.hardware_timestamp_domain.strip():
            raise ValueError("hardware_timestamp_domain must not be empty")
        if self.received_at_utc_ns <= 0 or self.received_at_monotonic_ns <= 0:
            raise ValueError("camera host timestamps must be positive")

    def detached_copy(self) -> DepthCameraFrame:
        """Return arrays detached from SDK-owned frame buffers."""

        return DepthCameraFrame(
            camera_name=self.camera_name,
            camera_serial=self.camera_serial,
            color_bgr=self.color_bgr.copy(),
            depth_uint16=self.depth_uint16.copy(),
            intrinsics=self.intrinsics.copy(),
            distortion_coefficients=self.distortion_coefficients.copy(),
            distortion_model=self.distortion_model,
            depth_scale_metres=self.depth_scale_metres,
            color_hardware_timestamp_ms=self.color_hardware_timestamp_ms,
            depth_hardware_timestamp_ms=self.depth_hardware_timestamp_ms,
            color_frame_number=self.color_frame_number,
            depth_frame_number=self.depth_frame_number,
            hardware_timestamp_domain=self.hardware_timestamp_domain,
            received_at_utc_ns=self.received_at_utc_ns,
            received_at_monotonic_ns=self.received_at_monotonic_ns,
            depth_aligned_to_color=self.depth_aligned_to_color,
        )
