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

    # 先创建 fan_temp 实体并注册
    fan_temp_ent = LeediFanTempThresholdNumber(client, config_entry)
    hass.data[DOMAIN][f"{config_entry.entry_id}_fan_temp_num"] = fan_temp_ent

    async_add_entities([
        fan_temp_ent,
        *channel_ents,
    ])


class LeediChannelNumber(NumberEntity):
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
        """灯是否开着：switch 状态 或 设备上报的 RGBW 非 0。"""
        sw = self.hass.data[DOMAIN].get(f"{self._entry.entry_id}_main_switch")
        if sw and sw._attr_is_on:
            return True
        # ★ 设备上报 RGBW 非 0 也算灯开
        return any(self._state.get(k, 0) > 0 for k in ("r", "g", "b", "w"))

    def _handle_notify(self, data: dict):
        if data.get("type") != "status":
            return
        self._attr_available = True

        # ★ 先更新 _state（供 _is_light_on 使用）
        for k in ("r", "g", "b", "w"):
            self._state[k] = data.get(k, 0)

        if self._ch_key == "uv":
            self.async_write_ha_state()
            return

        if time.time() - self._last_cmd_time < CMD_COOLDOWN:
            self.async_write_ha_state()
            return

        device_on = any(data.get(k, 0) > 0 for k in ("r", "g", "b", "w"))
        if not device_on:
            self.async_write_ha_state()
            return

        new_val = data.get(self._ch_key, 0)
        if self._attr_native_value != new_val:
            self._attr_native_value = new_val
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float):
        val = int(value)
        prev_val = self._attr_native_value
        prev_state = self._state.get(self._ch_key, 0)
        self._attr_native_value = val
        self._state[self._ch_key] = val
        self.async_write_ha_state()

        if not self._is_light_on():
            _LOGGER.debug("灯关闭，通道 %s = %d 暂存", self._ch_key, val)
            return

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
            except ValueError:
                pass

        # ★ 尝试从 fan 同步（若 fan 已加载）
        fan_ent = self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_fan_entity"
        )
        if fan_ent is not None:
            try:
                fan_ent._temp_threshold = int(self._attr_native_value)
                fan_ent.async_write_ha_state()
            except Exception:
                pass

    async def async_set_native_value(self, value: float):
        temp = int(value)
        fan_ent = self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_fan_entity"
        )
        old_temp = self._attr_native_value

        self._attr_native_value = temp
        if fan_ent is not None:
            fan_ent._temp_threshold = temp

        if fan_ent is not None and fan_ent.is_on:
            gear = {"low": 1, "high": 2}[fan_ent.preset_mode]
        else:
            gear = 0

        try:
            await self._client.set_fan(temp, gear)
            self._attr_available = True
            if fan_ent is not None:
                fan_ent.async_write_ha_state()
        except Exception as e:
            _LOGGER.warning("设置风扇温度阈值失败: %s", e)
            self._attr_native_value = old_temp
            if fan_ent is not None:
                fan_ent._temp_threshold = int(old_temp)
            self._attr_available = True
        self.async_write_ha_state()