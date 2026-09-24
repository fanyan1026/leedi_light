import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _validate_hhmm(s: str) -> bool:
    try:
        parts = s.split(":")
        if len(parts) < 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h < 24 and 0 <= m < 60
    except Exception:
        return False


def _build_options_schema(options: dict) -> vol.Schema:
    fields = {}

    # ---------- 3 个预设的独立功率 ----------
    for key, default in (
        ("preset_mix_gear", 100),
        ("preset_green_gear", 100),
        ("preset_red_gear", 100),
    ):
        fields[vol.Optional(key, default=options.get(key, default))] = \
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=100, step=1, unit_of_measurement="%",
                mode=selector.NumberSelectorMode.SLIDER,
            ))

    # ---------- 定时器 1 ----------
    fields[vol.Optional("timer1_enable",
                        default=options.get("timer1_enable", False))] = \
        selector.BooleanSelector()
    fields[vol.Optional("timer1_start",
                        default=options.get("timer1_start", "08:00"))] = \
        selector.TextSelector()
    fields[vol.Optional("timer1_end",
                        default=options.get("timer1_end", "21:00"))] = \
        selector.TextSelector()
    for key, default in (("timer1_open_dur", 20), ("timer1_close_dur", 20)):
        fields[vol.Optional(key, default=options.get(key, default))] = \
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=60, step=5, unit_of_measurement="分钟",
                mode=selector.NumberSelectorMode.SLIDER,
            ))

    # ---------- 定时器 2 ----------
    fields[vol.Optional("timer2_enable",
                        default=options.get("timer2_enable", False))] = \
        selector.BooleanSelector()
    fields[vol.Optional("timer2_start",
                        default=options.get("timer2_start", ""))] = \
        selector.TextSelector()
    fields[vol.Optional("timer2_end",
                        default=options.get("timer2_end", ""))] = \
        selector.TextSelector()
    for key, default in (("timer2_open_dur", 0), ("timer2_close_dur", 0)):
        fields[vol.Optional(key, default=options.get(key, default))] = \
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=0, max=60, step=5, unit_of_measurement="分钟",
                mode=selector.NumberSelectorMode.SLIDER,
            ))

    return vol.Schema(fields)


class LeediLightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_bluetooth(self, discovery_info):
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"LEDI AT5 80W ({self._discovery_info.address[-5:]})",
                data={"address": self._discovery_info.address},
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._discovery_info.name},
        )

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            address = user_input["address"]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"LEDI AT5 80W ({address[-5:]})",
                data={"address": address},
            )
        discoveries = async_discovered_service_info(self.hass, connectable=True)
        devices = {}
        for info in discoveries:
            name = info.name or ""
            if name.startswith("X-G-"):
                devices[info.address] = f"{name} ({info.address})"
        if not devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("address"): vol.In(devices)
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return LeediOptionsFlow(entry)


class LeediOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry

    async def async_step_init(self, user_input=None):
        errors = {}

        if user_input is not None:
            for slot in (1, 2):
                for key in (f"timer{slot}_start", f"timer{slot}_end"):
                    t = (user_input.get(key) or "").strip()
                    if t and not _validate_hhmm(t):
                        errors[key] = "invalid_time"

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(dict(self._entry.options)),
            errors=errors,
        )