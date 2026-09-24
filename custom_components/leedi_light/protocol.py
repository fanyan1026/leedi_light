import struct

HEADER = bytes([0x34, 0x43, 0x88, 0x88])
NOTIFY_HW = "4334888801"
NOTIFY_STATUS = "4334888802"


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_command(cmd: int, data: bytes = b"") -> bytes:
    payload = HEADER + bytes([cmd]) + struct.pack(">H", len(data)) + data
    crc = modbus_crc16(payload)
    return payload + struct.pack(">H", crc)


def parse_notify(hex_str: str):
    """解析设备通知帧。

    - HW 帧:  43348888 01 <len> <series><ntc><hw3><fw3> <crc>
    - 状态帧: 43348888 02 <len> <r><g><b><w><uv>[<temp>[<gear>]] <crc>
      UV=0xFE 为固件"不上报 UV"标记，非真实亮度
    """
    result = {}
    if hex_str.startswith(NOTIFY_HW):
        result["type"] = "hardware"
        try:
            result["series"] = int(hex_str[14:16], 16)
            result["ntc"] = int(hex_str[16:18], 16) == 1
            result["hardware_version"] = _parse_version(hex_str[18:24])
            result["firmware_version"] = _parse_version(hex_str[24:30])
        except Exception:
            pass
    elif hex_str.startswith(NOTIFY_STATUS):
        result["type"] = "status"
        try:
            data_len = int(hex_str[10:14], 16)
            light_hex = hex_str[14:24]
            r = int(light_hex[0:2], 16)
            g = int(light_hex[2:4], 16)
            b = int(light_hex[4:6], 16)
            w = int(light_hex[6:8], 16)
            uv_raw = int(light_hex[8:10], 16)

            result.update(r=r, g=g, b=b, w=w)

            # UV：0xFE 表示"固件不上报 UV"，调用方应保留本地值
            result["uv_valid"] = (uv_raw != 0xFE)
            result["uv"] = uv_raw if uv_raw != 0xFE else 0

            # 关灯判定：R/G/B/W 全 0 才算关，忽略 UV
            result["is_on"] = not (r == 0 and g == 0 and b == 0 and w == 0)

            if data_len >= 6:
                result["temp"] = int(hex_str[24:26], 16)
            if data_len >= 7:
                result["fan_gear"] = int(hex_str[26:28], 16)
        except Exception:
            pass
    return result


def _parse_version(hex_part: str) -> str:
    if len(hex_part) != 6:
        return ""
    a = int(hex_part[0:2], 16)
    b = int(hex_part[2:4], 16)
    c = int(hex_part[4:6], 16)
    return f"{a}.{b}.{c}"