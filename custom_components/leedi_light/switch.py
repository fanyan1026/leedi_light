import asyncio
import logging
import time
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CMD_COOLDOWN = 2.0


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]
    ent = LeediMainPowerSwitch(client, config_entry)
    hass.data[DOMAIN][f"{config_entry.entry_id}_main_switch"] = ent
    async_add_entities([ent])


class LeediMainPowerSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "main_power"
    _attr_should_poll = False

    def __init__(self, client, entry):
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_main_power"
        self._attr_available = True
        self._attr_is_on = False
        self._last_cmd_time = 0.0
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        client.set_notify_callback(self._handle_notify)
        client.set_disconnect_callback(self._handle_disconnect)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"

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
        # 下发命令后 2 秒内不按设备状态回滚（避免延迟生效导致闪回）
        if time.time() - self._last_cmd_time < CMD_COOLDOWN:
            self.async_write_ha_state()
            return
        # 只看 R/G/B/W，忽略 UV
        rgbw_on = any(data.get(k, 0) > 0 for k in ("r", "g", "b", "w"))
        if self._attr_is_on != rgbw_on:
            self._attr_is_on = rgbw_on
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._attr_is_on

    def _get_fan(self):
        return self.hass.data[DOMAIN].get(f"{self._entry.entry_id}_fan_entity")

    async def _set_power(self, enable: bool):
        prev_on = self._attr_is_on
        # 乐观更新
        self._attr_is_on = enable
        self._last_cmd_time = time.time()
        self.async_write_ha_state()
        try:
            if enable:
                # 开灯：下发保存的亮度，不再自动打开风扇！对齐小程序
                state = self.hass.data[DOMAIN].get(
                    f"{self._entry.entry_id}_channel_state"
                )
                if state:
                    r = state.get("r", 0)
                    g = state.get("g", 0)
                    b = state.get("b", 0)
                    w = state.get("w", 0)
                    uv = state.get("uv", 0)
                    try:
                        await self._client.set_brightness(r, g, b, w, uv)
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        _LOGGER.warning("开灯前设置亮度失败: %s", e)
                await self._client.set_main_power(True)
            else:
                await self._client.set_main_power(False)

            self._attr_available = True
            # 只关灯联动关闭风扇，开灯不开启风扇温控
            fan_ent = self._get_fan()
            if fan_ent is not None and not enable:
                try:
                    await fan_ent.async_turn_off()
                except Exception as e:
                    _LOGGER.warning("联动关闭风扇失败: %s", e)

            # 主动查询校验（延迟一会，让设备先响应）
            try:
                await asyncio.sleep(0.5)
                await self._client.query_status()
            except Exception as e:
                _LOGGER.debug("状态校验失败: %s", e)
        except Exception as e:
            _LOGGER.warning("总电源%s失败，回滚 UI: %s",
                            "开启" if enable else "关闭", e)
            self._attr_is_on = prev_on
            self._attr_available = True
            self._last_cmd_time = 0.0
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._set_power(True)

    async def async_turn_off(self, **kwargs):
        await self._set_power(False)
