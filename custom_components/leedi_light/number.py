import logging
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

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

    - 灯开：显示设备真实值，可拖，下发立即生效
    - 灯关：显示上次设置值，也可拖，下发后设备存着，下次开灯用
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
        self._light_on = False
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

    def _handle_notify(self, data: dict):
        if data.get("type") != "status":
            return
        self._attr_available = True

        light_on = bool(data.get("is_on", False))
        self._light_on = light_on

        # 灯关闭：保留滑块显示的"设置值"不变
        # 理由：灯关时设备会上报 RGBW=0，如果覆盖用户就看不到自己设的值
        if not light_on:
            self.async_write_ha_state()
            return

        # 灯开启：从设备读真实值
        if self._ch_key == "uv":
            new_val = self._state.get("uv", 0)   # UV 设备不报，用本地值
        else:
            new_val = data.get(self._ch_key, 0)

        if self._attr_native_value != new_val:
            self._attr_native_value = new_val
            self._state[self._ch_key] = new_val
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float):
        val = int(value)
        self._attr_native_value = val
        self._state[self._ch_key] = val

        # 不论灯开关，都下发 CMD=3
        # 灯开 → 立即生效
        # 灯关 → 设备存着，下次开灯用
        r = self._state.get("r", 0)
        g = self._state.get("g", 0)
        b = self._state.get("b", 0)
        w = self._state.get("w", 0)
        uv = self._state.get("uv", 0)
        try:
            await self._client.set_brightness(r, g, b, w, uv)
            self._attr_available = True
        except Exception as e:
            _LOGGER.warning("下发通道 %s 失败: %s", self._ch_key, e)
            self._attr_available = False
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
        gear = 0
        if fan_ent is not None:
            gear = {None: 0, "off": 0, "low": 1, "high": 2}.get(
                fan_ent.preset_mode, 0
            )
            fan_ent._temp_threshold = temp

        try:
            await self._client.set_fan(temp, gear)
            self._attr_native_value = temp
            self._attr_available = True
            if fan_ent is not None:
                fan_ent.async_write_ha_state()
        except Exception as e:
            _LOGGER.warning("设置风扇温度阈值失败: %s", e)
            self._attr_available = False
        self.async_write_ha_state()