import logging
import time

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CMD_COOLDOWN = 2.0

CHANNEL_MAP = [
    {"key": "r",  "trans_key": "channel_r"},
    {"key": "g",  "trans_key": "channel_g"},
    {"key": "b",  "trans_key": "channel_b"},
    {"key": "w",  "trans_key": "channel_w"},
    {"key": "uv", "trans_key": "channel_uv"},
]


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]
    state = hass.data[DOMAIN][f"{config_entry.entry_id}_channel_state"]

    channel_ents = [
        LeediChannelNumber(client, config_entry, state, ch["key"], ch["trans_key"])
        for ch in CHANNEL_MAP
    ]
    hass.data[DOMAIN][f"{config_entry.entry_id}_channel_entities"] = channel_ents

    async_add_entities([
        LeediFanTempThresholdNumber(client, config_entry),
        *channel_ents,
    ])


class LeediChannelNumber(NumberEntity):
    """5 路通道滑块。

    - 灯开：拖动 → 立即下发；notify 跟设备（2 秒冷却）
    - 灯关：拖动 → 只改本地预览，不下发
    """
    _attr_has_entity_name = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = "%"
    _attr_should_poll = False

    def __init__(self, client, entry, state, ch_key, trans_key):
        self._client = client
        self._entry = entry
        self._state = state
        self._ch_key = ch_key
        self._attr_translation_key = trans_key
        self._attr_unique_id = f"{entry.entry_id}_ch_{ch_key}"
        self._attr_native_value = 0
        self._attr_available = True
        self._last_cmd_time = 0.0
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        client.set_notify_callback(self._handle_notify)
        client.set_disconnect_callback(self._handle_disconnect)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._attr_native_value = self._state.get(self._ch_key, 0)

    async def async_will_remove_from_hass(self):
        self._client.remove_notify_callback(self._handle_notify)
        self._client.remove_disconnect_callback(self._handle_disconnect)

    def _handle_disconnect(self):
        if self._attr_available:
            self._attr_available = False
            self.async_write_ha_state()

    def _is_light_on(self) -> bool:
        sw = self.hass.data[DOMAIN].get(f"{self._entry.entry_id}_main_switch")
        return bool(sw and sw._attr_is_on)

    def _handle_notify(self, data: dict):
        if data.get("type") != "status":
            return
        self._attr_available = True

        # UV 设备不上报，保留本地值
        if self._ch_key == "uv":
            self.async_write_ha_state()
            return

        # 灯关 → 保留本地预览
        if not self._is_light_on():
            self.async_write_ha_state()
            return

        # 命令后 2 秒内不覆盖（防闪回）
        if time.time() - self._last_cmd_time < CMD_COOLDOWN:
            self.async_write_ha_state()
            return

        # 灯开 → 跟设备真实值
        new_val = data.get(self._ch_key, 0)
        if self._attr_native_value != new_val:
            self._attr_native_value = new_val
            self._state[self._ch_key] = new_val
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float):
        val = int(value)
        prev_val = self._attr_native_value
        prev_state = self._state.get(self._ch_key, 0)

        # 乐观更新 UI
        self._attr_native_value = val
        self._state[self._ch_key] = val
        self.async_write_ha_state()

        # 灯关 → 只改本地，不下发
        if not self._is_light_on():
            _LOGGER.debug("灯关闭，通道 %s = %d 暂存（下次开灯生效）",
                          self._ch_key, val)
            return

        # 灯开 → 实时下发
        r = self._state.get("r", 0)
        g = self._state.get("g", 0)
        b = self._state.get("b", 0)
        w = self._state.get("w", 0)
        uv = self._state.get("uv", 0)
        try:
            await self._client.set_brightness(r, g, b, w, uv)
            self._attr_available = True
            self._last_cmd_time = time.time()
        except Exception as e:
            _LOGGER.warning("下发通道 %s 失败，回滚: %s", self._ch_key, e)
            self._attr_native_value = prev_val
            self._state[self._ch_key] = prev_state
            self._attr_available = True
        self.async_write_ha_state()


class LeediFanTempThresholdNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "fan_temp_threshold"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "°C"
    _attr_mode = NumberMode.SLIDER
    _attr_should_poll = False

    def __init__(self, client, entry):
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_fan_temp_threshold"
        self._attr_native_value = 30
        self._attr_available = True
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = float(last.state)
                fan_ent = self.hass.data[DOMAIN].get(
                    f"{self._entry.entry_id}_fan_entity"
                )
                if fan_ent is not None:
                    fan_ent._temp_threshold = int(self._attr_native_value)
            except ValueError:
                pass

    async def async_set_native_value(self, value: float):
        temp = int(value)
        fan_ent = self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_fan_entity"
        )
        if fan_ent is None:
            return

        old_temp = fan_ent._temp_threshold
        fan_ent._temp_threshold = temp

        if fan_ent.is_on:
            gear = {"low": 1, "high": 2}[fan_ent.preset_mode]
        else:
            gear = 0

        try:
            await self._client.set_fan(temp, gear)
            self._attr_native_value = temp
            self._attr_available = True
            if fan_ent is not None:
                fan_ent.async_write_ha_state()
        except Exception as e:
            _LOGGER.warning("设置风扇温度阈值失败: %s", e)
            fan_ent._temp_threshold = old_temp
            self._attr_available = True
        self.async_write_ha_state()