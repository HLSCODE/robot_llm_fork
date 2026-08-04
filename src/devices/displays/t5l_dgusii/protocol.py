from __future__ import annotations

from collections.abc import Iterable

FRAME_HEADER = bytes([0x5A, 0xA5])
WRITE_VAR_CMD = 0x82


class DgusProtocolError(ValueError):
    pass


def check_byte(value: int, name: str = "value") -> int:
    if not 0 <= value <= 0xFF:
        raise DgusProtocolError(f"{name} must be in 0x00~0xFF: 0x{value:X}")
    return value


def check_word(value: int, name: str = "value") -> int:
    if not 0 <= value <= 0xFFFF:
        raise DgusProtocolError(f"{name} must be in 0x0000~0xFFFF: 0x{value:X}")
    return value


def word_to_bytes(value: int) -> bytes:
    return check_word(value).to_bytes(2, byteorder="big", signed=False)


def words_to_payload(values: Iterable[int]) -> bytes:
    return b"".join(word_to_bytes(value) for value in values)


def bytes_to_payload(values: Iterable[int]) -> bytes:
    return bytes(check_byte(value) for value in values)


def build_write_frame(addr: int, payload: bytes) -> bytes:
    check_word(addr, "addr")

    length = 1 + 2 + len(payload)
    check_byte(length, "length")

    return FRAME_HEADER + bytes([length, WRITE_VAR_CMD]) + word_to_bytes(addr) + payload


def build_write_words_frame(addr: int, values: Iterable[int]) -> bytes:
    return build_write_frame(addr, words_to_payload(values))


def build_write_bytes_frame(addr: int, values: Iterable[int]) -> bytes:
    return build_write_frame(addr, bytes_to_payload(values))


def format_frame(frame: bytes) -> str:
    return frame.hex(" ").upper()
