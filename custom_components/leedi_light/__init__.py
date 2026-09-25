import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .ble_client import LeediBleClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "select", "number", "sensor", "fan", "button"]

TIMER_KEYS = (
    "timer1_enable", "timer1_start", "timer1_end",
    "timer1_open_dur", "timer1_close_dur",
    "timer2_enable", "timer2_start", "timer2_end",
    "timer2_open_dur", "timer2_close_dur",
)

SYNC_DELAY_SEC = 2


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
    hass.data[DOMAIN][f"{entry.entry_id}_fan_temp_num"] = None
    hass.data[DOMAIN][f"{entry.entry_id}_main_switch"] = None
    hass.data[DOMAIN][f"{entry.entry_id}_timer_cache"] = {}
    hass.data[DOMAIN][f"{entry.entry_id}_timer_unsubs"] = []

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await client.start()
    hass.async_create_task(client.poll_now())

    _setup_timer_syncs(hass, entry, client)

    entry.async_on_unload(entry.add_update_listener(_options_updated))

    hass.async_create_task(_apply_timers_from_options(hass, entry))
    _cache_timer_options(hass, entry)

    return True


def _setup_timer_syncs(hass: HomeAssistant, entry: ConfigEntry, client):
    for unsub in hass.data[DOMAIN].get(f"{entry.entry_id}_timer_unsubs", []):
        try:
            unsub()
        except Exception:
            pass

    opts = entry.options
    enabled_timers = [
        slot for slot in (1, 2)
        if opts.get(f"timer{slot}_enable", False)
    ]

    if not enabled_timers:
        _LOGGER.info("没有启用的定时器，跳过时刻同步注册")
        hass.data[DOMAIN][f"{entry.entry_id}_timer_unsubs"] = []
        return

    unsubs = []
    for slot in enabled_timers:
        for boundary in ("start", "end"):
            t = (opts.get(f"timer{slot}_{boundary}") or "").strip()
            if not t:
                continue
            parsed = _parse_hhmm(t)
            if parsed is None:
                continue
            h, m = parsed

            async def _cb(now, _client=client, _h=h, _m=m, _slot=slot, _b=boundary):
                _LOGGER.info(
                    "定时器%d %s 时刻 %02d:%02d → 主动同步设备状态",
                    _slot, _b, _h, _m
                )
                try:
                    await _client._poll_once()
                except Exception as e:
                    _LOGGER.debug("定时器同步失败: %s", e)

            unsub = async_track_time_change(
                hass, _cb, hour=h, minute=m, second=SYNC_DELAY_SEC
            )
            unsubs.append(unsub)
            _LOGGER.info(
                "已注册时刻同步：定时器%d %s @ %02d:%02d:%02d",
                slot, boundary, h, m, SYNC_DELAY_SEC
            )

    hass.data[DOMAIN][f"{entry.entry_id}_timer_unsubs"] = unsubs
    _LOGGER.info("共注册 %d 个时刻同步", len(unsubs))


def _parse_hhmm(s: str):
    try:
        parts = s.split(":")
        if len(parts) < 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and 0 <= m < 60):
            return None
        return (h, m)
    except Exception:
        return None


def _cache_timer_options(hass: HomeAssistant, entry: ConfigEntry):
    opts = entry.options
    cache = {k: opts.get(k) for k in TIMER_KEYS}
    hass.data[DOMAIN][f"{entry.entry_id}_timer_cache"] = cache


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry):
    opts = entry.options
    new_cache = {k: opts.get(k) for k in TIMER_KEYS}
    old_cache = hass.data[DOMAIN].get(f"{entry.entry_id}_timer_cache", {})

    client = hass.data[DOMAIN].get(entry.entry_id)

    if old_cache != new_cache:
        hass.data[DOMAIN][f"{entry.entry_id}_timer_cache"] = new_cache
        await _apply_timers_from_options(hass, entry)
        _LOGGER.info("定时器配置变更，已重新下发")

        if client is not None:
            _setup_timer_syncs(hass, entry, client)

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
        for unsub in hass.data[DOMAIN].get(f"{entry.entry_id}_timer_unsubs", []):
            try:
                unsub()
            except Exception:
                pass

        client = hass.data[DOMAIN].pop(entry.entry_id, None)
        for key in ("channel_state", "channel_entities", "profile_entity",
                    "fan_entity", "fan_temp_num", "main_switch",
                    "timer_cache", "timer_unsubs"):
            hass.data[DOMAIN].pop(f"{entry.entry_id}_{key}", None)
        if client is not None:
            await client.stop()
    return unload_ok