"""Tapping relay exports."""

from .client import TappingDevice
from .registers import (
    DEFAULT_TAPPING_CHANNEL_COUNT,
    DEFAULT_TAPPING_DEVICE_ADDRESS,
    MAX_TAPPING_CHANNEL_COUNT,
    channel_to_coil,
)

StatefulTappingDevice = TappingDevice
TappingDeviceSnapshot = object

