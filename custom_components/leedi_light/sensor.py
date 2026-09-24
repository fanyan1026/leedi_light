import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfPower

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]
    state = hass.data[DOMAIN][f"{config_entry.entry_id}_channel_state"]

    async_add_entities([
        LeediEstPowerSensor(client, config_entry, state),
    ])


class LeediEstPowerSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "est_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_should_poll = False

    def __init__(self, client, entry, state: dict):
        self._client = client
        self._entry = entry
        self._state = state
        self._attr_unique_id = f"{entry.entry_id}_est_power"
        self._attr_native_value = 0.0
        self._attr_available = True
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        client.set_notify_callback(self._handle_notify)
        client.set_disconnect_callback(self._handle_disconnect)

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

        # 灯关 → 0W
        if not self._is_light_on():
            if self._attr_native_value != 0.0:
                self._attr_native_value = 0.0
                self.async_write_ha_state()
            return

        # 灯开 → 用 5 路实际值（RGBW 从设备，UV 从本地）
        r = data.get("r", 0)
        g = data.get("g", 0)
        b = data.get("b", 0)
        w = data.get("w", 0)
        uv = self._state.get("uv", 0)

        total = r + g + b + w + uv
        est = round(80.0 * total / 500.0, 1)

        if self._attr_native_value != est:
            self._attr_native_value = est
            self.async_write_ha_state()
