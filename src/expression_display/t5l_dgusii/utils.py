from __future__ import annotations


def parse_int(text: str | int) -> int:
    if isinstance(text, int):
        return text
    value = str(text).strip()
    if value.lower().startswith("0x"):
        return int(value, 16)
    return int(value, 10)
