import logging
from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([LeediMainPowerSwitch(client, config_entry)])


class LeediMainPowerSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "main_power"
    _attr_should_poll = False

    def __init__(self, client, entry):
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_main_power"
        self._attr_available = True
        self._attr_is_on = False
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        client.set_notify_callback(self._handle_notify)
        client.set_disconnect_callback(self._handle_disconnect)

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
        self._attr_is_on = bool(data.get("is_on", False))
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._attr_is_on

    def _get_fan(self):
        return self.hass.data[DOMAIN].get(f"{self._entry.entry_id}_fan_entity")

    async def _set_power(self, enable: bool):
        prev = self._attr_is_on
        self._attr_is_on = enable
        self.async_write_ha_state()

        # 1) 总电源
        try:
            await self._client.set_main_power(enable)
            self._attr_available = True
        except Exception as e:
            _LOGGER.warning("总电源%s失败: %s",
                            "开启" if enable else "关闭", e)
            self._attr_is_on = prev
            self._attr_available = False
            self.async_write_ha_state()
            return

        # 2) 联动风扇
        fan_ent = self._get_fan()
        if fan_ent is not None:
            try:
                if enable:
                    await fan_ent.async_turn_on()
                else:
                    await fan_ent.async_turn_off()
            except Exception as e:
                _LOGGER.warning("联动风扇失败: %s", e)

        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._set_power(True)

    async def async_turn_off(self, **kwargs):
        await self._set_power(False)