"""保存/重设按钮。"""
import logging

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    async_add_entities([
        LeediSaveSlotButton(hass, config_entry),
        LeediRevertButton(hass, config_entry),
    ])


async def _notify(hass, title: str, message: str, nid: str):
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"title": title, "message": message, "notification_id": nid},
        blocking=False,
    )


class LeediSaveSlotButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "save_slot"
    _attr_should_poll = False

    def __init__(self, hass, entry):
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_save_slot"
        self._attr_available = True
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    def _prof(self):
        return self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_profile_entity"
        )

    async def async_press(self):
        prof = self._prof()
        if prof is None:
            return
        slot = prof.get_slot_index()
        if slot < 0:
            await _notify(
                self.hass,
                title="无法保存",
                message=(
                    f"当前为**预设模式「{prof.current_option}」**。\n\n"
                    "预设模式下拖动的滑块只是**临时生效**。\n\n"
                    "如需保存，请先切到某个**自定义槽**。"
                ),
                nid=f"leedi_save_hint_{self._entry.entry_id}",
            )
            return

        snapshot = prof.snapshot_state()
        slot_num = slot + 1
        new_options = dict(self._entry.options)
        for ch in ("r", "g", "b", "w", "uv"):
            new_options[f"custom_{slot_num}_{ch}"] = snapshot[ch]

        # async_update_entry 不是协程，不能 await
        self.hass.config_entries.async_update_entry(
            self._entry, options=new_options
        )
        _LOGGER.info("已保存到自定义槽 %d: %s", slot_num, snapshot)
        await _notify(
            self.hass,
            title="已保存",
            message=(
                f"当前 5 路值已保存到**自定义槽 {slot_num}**：\n\n"
                f"- 红光：{snapshot['r']}%\n"
                f"- 绿光：{snapshot['g']}%\n"
                f"- 蓝光：{snapshot['b']}%\n"
                f"- 白光：{snapshot['w']}%\n"
                f"- UV：{snapshot['uv']}%"
            ),
            nid=f"leedi_save_ok_{self._entry.entry_id}",
        )


class LeediRevertButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "revert"
    _attr_should_poll = False

    def __init__(self, hass, entry):
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_revert"
        self._attr_available = True
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_press(self):
        prof = self.hass.data[DOMAIN].get(
            f"{self._entry.entry_id}_profile_entity"
        )
        if prof is None:
            return
        slot = prof.get_slot_index()
        if slot >= 0:
            prof.restore_from_options()
            if prof._is_light_on():
                await prof._apply_and_send()
            else:
                prof._refresh_channels()
                prof.async_write_ha_state()
            _LOGGER.info("已重设光源到槽 %d", slot + 1)
            await _notify(
                self.hass,
                title="已重设",
                message=f"已恢复到**自定义槽 {slot + 1}**。",
                nid=f"leedi_revert_ok_{self._entry.entry_id}",
            )
            return
        await prof._recompute_and_apply()
        _LOGGER.info("预设模式已重设")
        await _notify(
            self.hass,
            title="已重设",
            message=f"已按**预设模式「{prof.current_option}」**重设。",
            nid=f"leedi_revert_ok_{self._entry.entry_id}",
        )