import asyncio
import logging
import time
from datetime import datetime
from typing import Callable, Dict, Any, List, Optional
from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from .protocol import parse_notify, build_command
from .const import (
    CHAR_UUID,
    CMD_GET_HW,
    CMD_GET_STATUS,
    CMD_SET_TIMER,
    CMD_SET_BRIGHTNESS,
    CMD_SWITCH,
    CMD_FAN,
)

_LOGGER = logging.getLogger(__name__)
CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 5
LOCK_WAIT_TIMEOUT = 6
SEND_RETRIES = 2               # 下发失败重试次数
POLL_INTERVAL = 600            # 每 10 分钟轮询一次
IDLE_TIMEOUT = 60              # 用户操作后 60 秒无动作 → 断开
IDLE_CHECK_INTERVAL = 30       # 每 30 秒检查一次空闲


class LeediBleClient:
    """乐迪灯 BLE 客户端。"""
    def __init__(self, hass, address: str):
        self.hass = hass
        self._address = address
        self._client: Optional[BleakClient] = None
        self._connected = False
        self._notify_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._disconnect_callbacks: List[Callable[[], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._closing = False
        self._closing_connection = False
        self._poll_task: Optional[asyncio.Task] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._last_user_activity = 0.0

    # ---------------- 属性 ----------------
    @property
    def is_connected(self) -> bool:
        return (
            self._connected
            and self._client is not None
            and self._client.is_connected
        )

    # ---------------- 回调注册 ----------------
    def set_notify_callback(self, cb):
        if cb not in self._notify_callbacks:
            self._notify_callbacks.append(cb)

    def remove_notify_callback(self, cb):
        if cb in self._notify_callbacks:
            self._notify_callbacks.remove(cb)

    def set_disconnect_callback(self, cb):
        if cb not in self._disconnect_callbacks:
            self._disconnect_callbacks.append(cb)

    def remove_disconnect_callback(self, cb):
        if cb in self._disconnect_callbacks:
            self._disconnect_callbacks.remove(cb)

    def set_connect_callback(self, cb):
        if cb not in self._connect_callbacks:
            self._connect_callbacks.append(cb)

    def remove_connect_callback(self, cb):
        if cb in self._connect_callbacks:
            self._connect_callbacks.remove(cb)

    # ---------------- 生命周期 ----------------
    async def start(self):
        self._closing = False
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_check_loop())

    async def stop(self):
        self._closing = True
        for t in (self._poll_task, self._idle_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._poll_task = None
        self._idle_task = None
        await self.disconnect()

    # ---------------- 后台循环 ----------------
    async def _poll_loop(self):
        """10 分钟周期轮询。首次 poll 由 poll_now 触发。"""
        while not self._closing:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                if self._closing:
                    return
                if not self.is_connected:
                    await self._poll_once()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("轮询循环异常", exc_info=True)

    async def _idle_check_loop(self):
        while not self._closing:
            try:
                await asyncio.sleep(IDLE_CHECK_INTERVAL)
                if self._closing:
                    return
                if self.is_connected:
                    idle = time.time() - self._last_user_activity
                    if idle > IDLE_TIMEOUT:
                        _LOGGER.debug("空闲 %d 秒，断开连接", int(idle))
                        await self.disconnect()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("空闲检查异常", exc_info=True)

    async def poll_now(self):
        """启动时调用：等 HA 就绪后再 poll。"""
        await asyncio.sleep(3)
        if self._closing:
            return
        try:
            await self._poll_once()
            _LOGGER.info("启动状态刷新完成")
        except Exception as e:
            _LOGGER.warning("启动状态刷新失败: %s", e)

    async def _poll_once(self):
        if self._closing:
            return
        try:
            ok = await self.connect()
            if not ok:
                self._notify_disconnect()
                return
            self._last_user_activity = time.time()
            await self.send_command_no_reconnect(CMD_GET_STATUS)
            await asyncio.sleep(0.5)
        except Exception as e:
            _LOGGER.debug("轮询失败: %s", e)
            self._notify_disconnect()

    def _notify_disconnect(self):
        for cb in list(self._disconnect_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.debug("断开回调异常", exc_info=True)

    # ---------------- 底层事件 ----------------
    def _on_disconnect(self, _client):
        if self._closing_connection:
            return
        _LOGGER.info("乐迪灯 %s 蓝牙意外断开", self._address)
        self._connected = False
        self._client = None
        self._notify_disconnect()

    def _notification_handler(self, sender, data: bytes):
        hex_str = bytes(data).hex().upper()
        parsed = parse_notify(hex_str)
        if not parsed:
            return
        # 收到status上报，刷新空闲计时器，风扇自动启停不会被空闲断开
        if parsed.get("type") == "status":
            self._last_user_activity = time.time()

        if parsed.get("type") != "status":
            _LOGGER.debug("乐迪灯通知: %s", hex_str)
        for cb in list(self._notify_callbacks):
            try:
                cb(parsed)
            except Exception:
                _LOGGER.debug("通知回调异常", exc_info=True)

    # ---------------- 连接 ----------------
    async def connect(self) -> bool:
        if self.is_connected:
            return True
        async with self._connect_lock:
            if self.is_connected:
                return True
            try:
                return await asyncio.wait_for(
                    self._connect_inner(), timeout=CONNECT_TIMEOUT
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("连接设备 %s 超时", self._address)
            except Exception as e:
                _LOGGER.warning("连接设备 %s 失败: %s", self._address, e)
            self._connected = False
            self._client = None
            return False

    async def _connect_inner(self) -> bool:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if device is None:
            raise RuntimeError(f"找不到设备 {self._address}")
        self._client = await establish_connection(
            BleakClient,
            device,
            self._address,
            max_attempts=2,
            disconnected_callback=self._on_disconnect,
        )
        await self._client.start_notify(CHAR_UUID, self._notification_handler)
        self._connected = True
        _LOGGER.info("乐迪灯 %s 已连接", self._address)
        await asyncio.sleep(0.2)
        try:
            now = datetime.now()
            await self.send_command_no_reconnect(
                CMD_GET_HW, bytes([now.hour, now.minute, now.second])
            )
            await asyncio.sleep(0.15)
            await self.send_command_no_reconnect(CMD_GET_STATUS)
        except Exception:
            _LOGGER.debug("初始查询状态失败", exc_info=True)
        for cb in list(self._connect_callbacks):
            try:
                cb()
            except Exception:
                pass
        return True

    async def disconnect(self):
        self._closing_connection = True
        try:
            if self._client:
                try:
                    await self._client.stop_notify(CHAR_UUID)
                except Exception:
                    pass
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
        finally:
            self._connected = False
            self._client = None
            self._closing_connection = False

    # ---------------- 发送 ----------------
    async def _send_raw(self, cmd: int, data: bytes = b""):
        if not self.is_connected:
            raise ConnectionError("设备断开，无法写入")
        packet = build_command(cmd, data)
        if cmd != CMD_GET_STATUS:
            _LOGGER.debug("乐迪灯写入: %s", packet.hex().upper())
        try:
            await self._client.write_gatt_char(CHAR_UUID, packet, response=True)
        except BleakError as e:
            self._connected = False
            self._client = None
            raise ConnectionError(f"写入BLE失败: {e}") from e

    async def send_command(self, cmd: int, data: bytes = b"", retries: int = SEND_RETRIES):
        """用户操作：连接 → 下发。失败自动重试 retries 次。"""
        self._last_user_activity = time.time()
        last_err = None
        for attempt in range(retries + 1):
            try:
                if not self.is_connected:
                    ok = await self.connect()
                    if not ok:
                        raise ConnectionError("设备连接失败")
                await asyncio.wait_for(
                    self._lock.acquire(), timeout=LOCK_WAIT_TIMEOUT
                )
                try:
                    return await asyncio.wait_for(
                        self._send_raw(cmd, data), timeout=COMMAND_TIMEOUT
                    )
                finally:
                    self._lock.release()
            except asyncio.TimeoutError as e:
                last_err = e
            except (ConnectionError, BleakError) as e:
                last_err = e
                self._connected = False
                self._client = None
            except Exception as e:
                last_err = e
            if attempt < retries:
                _LOGGER.warning(
                    "下发失败（第 %d 次），重试... cmd=%d err=%s",
                    attempt + 1, cmd, last_err,
                )
                await asyncio.sleep(0.5)
        _LOGGER.error("下发失败（已重试 %d 次）cmd=%d err=%s",
                      retries, cmd, last_err)
        raise ConnectionError(f"下发失败: {last_err}")

    async def send_command_no_reconnect(self, cmd: int, data: bytes = b""):
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=LOCK_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError("获取发送锁超时")
        try:
            return await asyncio.wait_for(
                self._send_raw(cmd, data), timeout=COMMAND_TIMEOUT
            )
        finally:
            self._lock.release()

    # ---------------- 语义 API ----------------
    @staticmethod
    def _clamp(v, lo, hi):
        try:
            v = int(v)
        except Exception:
            v = lo
        return max(lo, min(hi, v))

    async def set_main_power(self, enable: bool):
        await self.send_command(CMD_SWITCH, bytes([1 if enable else 0]))

    async def set_brightness(self, r, g, b, w, uv):
        payload = bytes([
            self._clamp(r, 0, 100), self._clamp(g, 0, 100),
            self._clamp(b, 0, 100), self._clamp(w, 0, 100),
            self._clamp(uv, 0, 100),
        ])
        await self.send_command(CMD_SET_BRIGHTNESS, payload)

    async def set_fan(self, temp_thresh: int, gear: int):
        payload = bytes([
            self._clamp(temp_thresh, 0, 100), self._clamp(gear, 0, 2)
        ])
        await self.send_command(CMD_FAN, payload)

    async def query_status(self):
        await self.send_command(CMD_GET_STATUS)

    async def set_timer(self, slot, enable, start_h, start_m, end_h, end_m,
                        delay_enable, open_dur, close_dur):
        payload = bytes([
            self._clamp(slot, 1, 2),
            1 if enable else 0,
            self._clamp(start_h, 0, 23),
            self._clamp(start_m, 0, 59),
            self._clamp(end_h, 0, 23),
            self._clamp(end_m, 0, 59),
            1 if delay_enable else 0,
            self._clamp(open_dur, 0, 255),
            self._clamp(close_dur, 0, 255),
        ])
        await self.send_command(CMD_SET_TIMER, payload)
