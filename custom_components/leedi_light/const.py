import math

DOMAIN = "leedi_light"
# BLE UUID
SERVICE_UUID = "8332af20-6d0e-4eea-bb35-665544332211"
CHAR_UUID    = "8332af20-6d0e-4eea-bb35-665544332211"
# 命令码
CMD_GET_HW = 1
CMD_GET_STATUS = 2
CMD_SET_BRIGHTNESS = 3
CMD_SET_TIMER = 4
CMD_SWITCH = 5
CMD_FAN = 6
HEADER = bytes([0x34, 0x43, 0x88, 0x88])
NOTIFY_HW = "4334888801"
NOTIFY_STATUS = "4334888802"


def js_round(x: float) -> int:
    """对齐 JavaScript 的 Math.round（四舍五入，.5 向远离 0 的方向进）。

    - js_round(37.5) == 38
    - js_round(38.5) == 39
    - Python 内置 round(38.5) == 38（银行家舍入）——这里不用它
    """
    return math.floor(x + 0.5)



