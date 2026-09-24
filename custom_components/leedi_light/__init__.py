import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .ble_client import LeediBleClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["switch", "select", "number", "sensor", "fan", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    address = entry.data["address"]
    client = LeediBleClient(hass, address)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = client
    hass.data[DOMAIN][f"{entry.entry_id}_channel_state"] = {
        "r": 0, "g": 0, "b": 0, "w": 0, "uv": 0, "is_on": False,
    }
    hass.data[DOMAIN][f"{entry.entry_id}_channel_entities"] = []
    hass.data[DOMAIN][f"{entry.entry_id}_profile_entity"] = None
    hass.data[DOMAIN][f"{entry.entry_id}_fan_entity"] = None
    hass.data[DOMAIN][f"{entry.entry_id}_main_switch"] = None
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await client.start()
    # 启动后立即刷新状态（延迟 3 秒等 HA 完全就绪）
    hass.async_create_task(client.poll_now())
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    hass.async_create_task(_apply_timers_from_options(hass, entry))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry):
    await _apply_timers_from_options(hass, entry)
    prof_ent = hass.data[DOMAIN].get(f"{entry.entry_id}_profile_entity")
    if prof_ent is not None:
        await prof_ent.async_reload_from_options()


async def _apply_timers_from_options(hass: HomeAssistant, entry: ConfigEntry):
    client = hass.data[DOMAIN].get(entry.entry_id)
    if client is None:
        return
    opts = entry.options
    for slot in (1, 2):
        enabled = bool(opts.get(f"timer{slot}_enable", False))
        start = (opts.get(f"timer{slot}_start") or "").strip()
        end = (opts.get(f"timer{slot}_end") or "").strip()
        if not start or not end:
            continue
        try:
            sh, sm = [int(x) for x in start.split(":")[:2]]
            eh, em = [int(x) for x in end.split(":")[:2]]
        except Exception:
            continue
        open_dur = int(opts.get(f"timer{slot}_open_dur", 0) or 0)
        close_dur = int(opts.get(f"timer{slot}_close_dur", 0) or 0)
        try:
            await client.set_timer(
                slot=slot,
                enable=enabled,
                start_h=sh, start_m=sm,
                end_h=eh, end_m=em,
                delay_enable=(open_dur > 0 or close_dur > 0),
                open_dur=open_dur,
                close_dur=close_dur,
            )
            _LOGGER.info(
                "Timer %d applied: enable=%s %02d:%02d-%02d:%02d open=%d close=%d",
                slot, enabled, sh, sm, eh, em, open_dur, close_dur,
            )
        except Exception as e:
            _LOGGER.warning("Timer %d apply failed: %s", slot, e)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client = hass.data[DOMAIN].pop(entry.entry_id, None)
        for key in ("channel_state", "channel_entities", "profile_entity",
                    "fan_entity", "main_switch"):
            hass.data[DOMAIN].pop(f"{entry.entry_id}_{key}", None)
        if client is not None:
            await client.stop()
    return unload_ok
