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
            # ★ fan 自己 restore temp_threshold（跟 number 各存一份，值一致）
            t = last.attributes.get("temp_threshold")
            if isinstance(t, (int, float)):
                self._temp_threshold = int(t)

        # ★ 尝试从 number 同步（可能已加载），若未加载保留自己恢复的值
        self._temp_threshold = self._get_temp_from_number(fallback=self._temp_threshold)

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
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def preset_mode(self) -> str | None:
        return self._preset_mode

    def _get_temp_from_number(self, fallback=None):
        """从 number 实体读 temp_threshold（若已加载）。"""
        if fallback is None:
            fallback = self._temp_threshold
        num_ent = self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_fan_temp_num"
        )
        if num_ent is not None:
            try:
                return int(num_ent._attr_native_value)
            except Exception:
                pass
        return fallback

    @property
    def extra_state_attributes(self) -> dict[str, StateType]:
        # 用 number 的值（若已加载），否则自己的
        attrs = {"temp_threshold": self._get_temp_from_number()}
        if self._real_gear is not None:
            attrs["real_running_gear"] = self._real_gear
        return attrs

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in PRESET_MODES:
            return
        old_preset = self._preset_mode
        self._preset_mode = preset_mode
        gear = {PRESET_LOW: 1, PRESET_HIGH: 2}[preset_mode]

        if self._is_on:
            try:
                temp = self._get_temp_from_number()
                await self._client.set_fan(temp, gear)
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
            temp = self._get_temp_from_number()
            await self._client.set_fan(temp, target_gear)
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
            temp = self._get_temp_from_number()
            await self._client.set_fan(temp, 0)
            self._attr_available = True
        except Exception as e:
            _LOGGER.warning("关闭风扇失败: %s", e)
            self._is_on = old_is_on
            self._attr_available = False
        self.async_write_ha_state()