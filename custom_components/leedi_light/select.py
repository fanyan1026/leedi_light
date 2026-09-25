import logging
import time

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, js_round

_LOGGER = logging.getLogger(__name__)

CMD_COOLDOWN = 2.0

PROFILES = {
    "mix":   {"name": "混合光源", "r": 100, "g": 100, "b": 100, "w": 100, "uv": 100},
    "green": {"name": "青翠光源", "r": 75,  "g": 100, "b": 100, "w": 100, "uv": 100},
    "red":   {"name": "胭红光源", "r": 100, "g": 75,  "b": 100, "w": 100, "uv": 100},
}
CUSTOM_NAMES = ["自定义一", "自定义二", "自定义三"]
PROFILE_OPTIONS = [v["name"] for v in PROFILES.values()] + CUSTOM_NAMES
KEY_MAP = {v["name"]: k for k, v in PROFILES.items()}

GEAR_KEY_MAP = {
    "mix": "preset_mix_gear",
    "green": "preset_green_gear",
    "red": "preset_red_gear",
}

DEFAULT_SLOT = {"r": 100, "g": 100, "b": 100, "w": 100, "uv": 100}
CH_KEYS = ("r", "g", "b", "w", "uv")


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]
    state = hass.data[DOMAIN][f"{config_entry.entry_id}_channel_state"]
    ent = LeediProfileSelect(client, config_entry, state)
    hass.data[DOMAIN][f"{config_entry.entry_id}_profile_entity"] = ent
    async_add_entities([ent])


class LeediProfileSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "profile"
    _attr_options = PROFILE_OPTIONS
    _attr_should_poll = False

    def __init__(self, client, entry, state: dict):
        self._client = client
        self._entry = entry
        self._state = state
        self._attr_unique_id = f"{entry.entry_id}_profile"
        self._attr_current_option = PROFILES["mix"]["name"]
        self._attr_available = True
        self._active_slot = 0
        self._temp_power = 100.0
        self._last_cmd_time = 0.0
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._client.set_notify_callback(self._handle_notify)

        last = await self.async_get_last_state()
        if last and last.state in self._attr_options:
            self._attr_current_option = last.state
            if self._attr_current_option in CUSTOM_NAMES:
                self._active_slot = CUSTOM_NAMES.index(self._attr_current_option)

        if self._attr_current_option not in CUSTOM_NAMES:
            self._temp_power = self._get_configured_gear(self._attr_current_option)

        self._recompute_state()
        self._refresh_channels()

    async def async_will_remove_from_hass(self):
        self._client.remove_notify_callback(self._handle_notify)

    def _get_configured_gear(self, option: str) -> float:
        key = KEY_MAP.get(option)
        opt_key = GEAR_KEY_MAP.get(key)
        if opt_key is None:
            return 100.0
        try:
            return max(0.0, min(100.0, float(self._entry.options.get(opt_key, 100))))
        except Exception:
            return 100.0

    def _get_custom_slot(self, slot_idx: int) -> dict:
        opts = self._entry.options
        prefix = f"custom_{slot_idx + 1}_"
        result = {}
        for ch in CH_KEYS:
            try:
                result[ch] = max(0, min(100, int(opts.get(prefix + ch, 100))))
            except Exception:
                result[ch] = 100
        return result

    def _get_current_base(self) -> dict:
        opt = self._attr_current_option
        if opt in CUSTOM_NAMES:
            return self._get_custom_slot(self._active_slot)
        key = KEY_MAP.get(opt)
        return dict(PROFILES[key]) if key else dict(DEFAULT_SLOT)

    def _recompute_state(self):
        base = self._get_current_base()
        if self._attr_current_option in CUSTOM_NAMES:
            scale = 1.0
        else:
            scale = self._temp_power / 100.0
        self._state.update({
            "r": js_round(base["r"] * scale),
            "g": js_round(base["g"] * scale),
            "b": js_round(base["b"] * scale),
            "w": js_round(base["w"] * scale),
            "uv": js_round(base["uv"] * scale),
        })

    def _is_light_on(self) -> bool:
        sw = self.hass.data[DOMAIN].get(f"{self._entry.entry_id}_main_switch")
        return bool(sw and sw._attr_is_on)

    def _refresh_channels(self):
        for ent in self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_channel_entities", []
        ):
            ent._attr_native_value = self._state.get(ent._ch_key, 0)
            ent.async_write_ha_state()

    async def _apply_and_send(self):
        self._recompute_state()
        r = self._state["r"]
        g = self._state["g"]
        b = self._state["b"]
        w = self._state["w"]
        uv = self._state["uv"]
        try:
            await self._client.set_brightness(r, g, b, w, uv)
            self._attr_available = True
            self._last_cmd_time = time.time()
        except Exception as e:
            _LOGGER.warning("下发失败: %s", e)
            self._attr_available = False
        self._refresh_channels()
        self.async_write_ha_state()

    def _handle_notify(self, data: dict):
        if data.get("type") != "status":
            return
        self._attr_available = True

        # ★ 检查自己 + 所有通道滑块的冷却
        now = time.time()
        in_cooldown = (now - self._last_cmd_time) < CMD_COOLDOWN
        if not in_cooldown:
            for ent in self.hass.data[DOMAIN].get(
                f"{self._entry.entry_id}_channel_entities", []
            ):
                if (now - ent._last_cmd_time) < CMD_COOLDOWN:
                    in_cooldown = True
                    break

        if in_cooldown:
            self.async_write_ha_state()
            return

        if not self._is_light_on():
            self._refresh_channels()
            self.async_write_ha_state()
            return

        # 灯开 → RGBW 跟设备，UV 不重算（保留本地值）
        for k in ("r", "g", "b", "w"):
            self._state[k] = data.get(k, 0)
        self._state["is_on"] = True

        self._refresh_channels()
        self.async_write_ha_state()

    async def async_reload_from_options(self):
        old_state = dict(self._state)
        if self._attr_current_option not in CUSTOM_NAMES:
            self._temp_power = self._get_configured_gear(self._attr_current_option)
        self._recompute_state()
        changed = any(old_state.get(k) != self._state.get(k) for k in CH_KEYS)
        if changed and self._is_light_on():
            await self._apply_and_send()
        else:
            self._refresh_channels()
            self.async_write_ha_state()

    def get_slot_index(self) -> int:
        if self._attr_current_option not in CUSTOM_NAMES:
            return -1
        return CUSTOM_NAMES.index(self._attr_current_option)

    def snapshot_state(self) -> dict:
        return {k: self._state.get(k, 0) for k in CH_KEYS}

    def restore_from_options(self):
        if self._attr_current_option not in CUSTOM_NAMES:
            return
        slot = self._get_custom_slot(self._active_slot)
        self._state.update(slot)
        self._refresh_channels()
        self.async_write_ha_state()

    async def _recompute_and_apply(self):
        if self._attr_current_option not in CUSTOM_NAMES:
            self._temp_power = self._get_configured_gear(self._attr_current_option)
        self._recompute_state()
        if self._is_light_on():
            await self._apply_and_send()
        else:
            self._refresh_channels()
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        if option in CUSTOM_NAMES:
            self._active_slot = CUSTOM_NAMES.index(option)
            self._temp_power = 100.0
        else:
            self._temp_power = self._get_configured_gear(option)
        self._recompute_state()
        if self._is_light_on():
            await self._apply_and_send()
        else:
            self._refresh_channels()
            self.async_write_ha_state()