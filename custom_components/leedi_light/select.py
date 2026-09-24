import logging

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, js_round

_LOGGER = logging.getLogger(__name__)

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
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._client.set_notify_callback(self._handle_notify)

        last = await self.async_get_last_state()
        if last and last.state in self._attr_options:
            self._attr_current_option = last.state
            if self._attr_current_option in CUSTOM_NAMES:
                self._active_slot = CUSTOM_NAMES.index(self._attr_current_option)

        self._recompute_state()
        self._refresh_channels()

    async def async_will_remove_from_hass(self):
        self._client.remove_notify_callback(self._handle_notify)

    # ---------- 从 options 读值 ----------
    def _get_configured_gear(self, option: str) -> float:
        """从 entry.options 读该预设的配置功率；自定义模式返回 100。"""
        key = KEY_MAP.get(option)
        opt_key = GEAR_KEY_MAP.get(key)
        if opt_key is None:
            return 100.0
        try:
            return max(0.0, min(100.0, float(self._entry.options.get(opt_key, 100))))
        except Exception:
            return 100.0

    def _get_custom_slot(self, slot_idx: int) -> dict:
        """从 options 读自定义槽的 5 路值。"""
        opts = self._entry.options
        prefix = f"custom_{slot_idx + 1}_"
        result = {}
        for ch in CH_KEYS:
            key = prefix + ch
            try:
                result[ch] = max(0, min(100, int(opts.get(key, 100))))
            except Exception:
                result[ch] = 100
        return result

    def _get_current_base(self) -> dict:
        """取当前模式的基准 5 路值。"""
        opt = self._attr_current_option
        if opt in CUSTOM_NAMES:
            return self._get_custom_slot(self._active_slot)
        key = KEY_MAP.get(opt)
        return dict(PROFILES[key]) if key else dict(DEFAULT_SLOT)

    def _recompute_state(self):
        """根据当前模式和功率计算 _state。"""
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

    def _refresh_channels(self):
        """通知 5 个通道滑块刷新显示。"""
        for ent in self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_channel_entities", []
        ):
            ent._attr_native_value = self._state.get(ent._ch_key, 0)
            ent.async_write_ha_state()

    # ---------- 下发 ----------
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
        except Exception as e:
            _LOGGER.warning("下发失败: %s", e)
            self._attr_available = False
        self._refresh_channels()
        self.async_write_ha_state()

    # ---------- notify 回调 ----------
    def _handle_notify(self, data: dict):
        if data.get("type") != "status":
            return
        self._attr_available = True

        # R/G/B/W 用设备上报的真实值
        for k in ("r", "g", "b", "w"):
            self._state[k] = data.get(k, 0)

        # UV 设备不上报，用本地计算的设定值
        base = self._get_current_base()
        if self._attr_current_option in CUSTOM_NAMES:
            scale = 1.0
        else:
            scale = self._temp_power / 100.0
        self._state["uv"] = js_round(base["uv"] * scale)

        self._state["is_on"] = bool(data.get("is_on", False))
        self._refresh_channels()
        self.async_write_ha_state()

    # ---------- options 变更回调 ----------
    async def async_reload_from_options(self):
        """Options Flow 提交后重新读配置并下发。"""
        old_state = dict(self._state)

        if self._attr_current_option not in CUSTOM_NAMES:
            self._temp_power = self._get_configured_gear(self._attr_current_option)

        self._recompute_state()

        changed = any(
            old_state.get(k) != self._state.get(k) for k in CH_KEYS
        )

        if changed:
            await self._apply_and_send()
        else:
            self._refresh_channels()
            self.async_write_ha_state()

    # ---------- 给 button 用 ----------
    def get_slot_index(self) -> int:
        """当前自定义槽索引 0/1/2；非自定义返回 -1。"""
        if self._attr_current_option not in CUSTOM_NAMES:
            return -1
        return CUSTOM_NAMES.index(self._attr_current_option)

    def snapshot_state(self) -> dict:
        """快照当前 5 路值。"""
        return {k: self._state.get(k, 0) for k in CH_KEYS}

    def restore_from_options(self):
        """从 options 重读当前自定义槽，覆盖 _state。"""
        if self._attr_current_option not in CUSTOM_NAMES:
            return
        slot = self._get_custom_slot(self._active_slot)
        self._state.update(slot)
        self._refresh_channels()
        self.async_write_ha_state()

    # ---------- 给 button 用：重设光源 ----------
    async def _recompute_and_apply(self):
        """按当前模式和 options 配置重新计算 5 路并下发。

        用途：预设模式下点"重设光源"按钮 → 丢弃临时修改 → 恢复配置值。
        """
        if self._attr_current_option not in CUSTOM_NAMES:
            # 预设模式：重读配置功率
            self._temp_power = self._get_configured_gear(self._attr_current_option)
        await self._apply_and_send()

    # ---------- 切模式 ----------
    async def async_select_option(self, option: str) -> None:
        """切换模式：自动复位到该模式的配置值。"""
        self._attr_current_option = option
        if option in CUSTOM_NAMES:
            self._active_slot = CUSTOM_NAMES.index(option)
            self._temp_power = 100.0
        else:
            # 预设模式：读配置功率（不是上次临时值）
            self._temp_power = self._get_configured_gear(option)

        await self._apply_and_send()