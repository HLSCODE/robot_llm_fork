"""Simple tapping device relay register helpers."""

DEFAULT_TAPPING_DEVICE_ADDRESS = 1
DEFAULT_TAPPING_CHANNEL_COUNT = 8
MAX_TAPPING_CHANNEL_COUNT = 16


def channel_to_coil(channel: int) -> int:
    _validate_channel(channel)
    return int(channel) - 1


def _validate_channel(channel: int) -> None:
    if not 1 <= int(channel) <= MAX_TAPPING_CHANNEL_COUNT:
        raise ValueError(f"invalid tapping channel: {channel}")

