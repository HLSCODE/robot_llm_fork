"""Modbus CRC helpers."""


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(data: bytes) -> bytes:
    crc = modbus_crc(data)
    return data + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def verify_crc(data: bytes) -> bool:
    if len(data) < 3:
        return False
    body = data[:-2]
    expected = data[-2] | (data[-1] << 8)
    return modbus_crc(body) == expected

