import logging
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import StateType
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PRESET_LOW = "low"
PRESET_HIGH = "high"
PRESET_MODES = [PRESET_LOW, PRESET_HIGH]
DEFAULT_PRESET = PRESET_HIGH


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]
    ent = LeediFan(client, config_entry)
    hass.data[DOMAIN][f"{config_entry.entry_id}_fan_entity"] = ent
    async_add_entities([ent])


class LeediFan(FanEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "fan"
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = PRESET_MODES
    _attr_should_poll = False

    def __init__(self, client, entry):
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_fan"
        self._attr_available = True
        self._is_on = False
        self._preset_mode = DEFAULT_PRESET
        self._temp_threshold = 30
        self._real_gear = None
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        client.set_notify_callback(self._handle_notify)
        client.set_disconnect_callback(self._handle_disconnect)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._is_on = False
        if last is not None:
            self._is_on = last.state == "on"
            pm = last.attributes.get("preset_mode")
            if pm in PRESET_MODES:
                self._preset_mode = pm
        # temp_threshold 交给number实体restore，此处不再读取

    async def async_will_remove_from_hass(self):
        self._client.remove_notify_callback(self._handle_notify)
        self._client.remove_disconnect_callback(self._handle_disconnect)

    def _handle_disconnect(self):
        self._attr_available = False
        self.async_write_ha_state()

    def _handle_notify(self, data: dict):
        if data.get("type") != "status":
            return
        self._attr_available = True
        gear = data.get("fan_gear")
        if gear is not None:
            self._real_gear = gear
        # fan_gear仅展示，不修改 _is_on / preset / temp_threshold
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def preset_mode(self) -> str | None:
        return self._preset_mode

    @property
    def extra_state_attributes(self) -> dict[str, StateType]:
        attrs = {}
        if self._real_gear is not None:
            attrs["real_running_gear"] = self._real_gear
        return attrs

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in PRESET_MODES:
            return
        old_preset = self._preset_mode
        self._preset_mode = preset_mode
        gear = {PRESET_LOW: 1, PRESET_HIGH: 2}[preset_mode]

        # 只有温控开启才下发；关闭仅更新内存
        if self._is_on:
            try:
                await self._client.set_fan(self._temp_threshold, gear)
                self._attr_available = True
            except Exception as e:
                _LOGGER.warning("设置风扇预设失败: %s", e)
                self._preset_mode = old_preset
                self._attr_available = False
        self.async_write_ha_state()

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        if preset_mode and preset_mode in PRESET_MODES:
            self._preset_mode = preset_mode
        target_gear = {PRESET_LOW: 1, PRESET_HIGH: 2}[self._preset_mode]
        old_is_on = self._is_on
        self._is_on = True
        try:
            await self._client.set_fan(self._temp_threshold, target_gear)
            self._attr_available = True
        except Exception as e:
            _LOGGER.warning("开启风扇温控失败: %s", e)
            self._is_on = old_is_on
            self._attr_available = False
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        old_is_on = self._is_on
        self._is_on = False
        try:
            await self._client.set_fan(self._temp_threshold, 0)
            self._attr_available = True
        except Exception as e:
            _LOGGER.warning("关闭风扇失败: %s", e)
            self._is_on = old_is_on
            self._attr_available = False
        self.async_write_ha_state()
