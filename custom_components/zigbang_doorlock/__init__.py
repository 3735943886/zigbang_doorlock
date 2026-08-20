"""Zigbang Doorlock 통합 구성요소.

클라우드(id/pw/imei)는 config_flow 에서 도어락 목록을 얻을 때 1회만 쓰고, 런타임 상태갱신/제어는
전부 ../zigbang 로컬 relay 의 observer 포트를 통해 push 로 처리한다(iot_class: local_push).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import ACCESS_LABELS, CONF_HOST, CONF_LOCKS, CONF_PORT, DOMAIN, PLATFORMS, SIGNAL_EVENT
from .coordinator import ZigbangCoordinator
from .protocol import extract_basic_attrgroup_patch, extract_idpevent
from .relay_client import RelayClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    locks: list[dict[str, Any]] = entry.data[CONF_LOCKS]
    locks_by_tpid = {lock["tp_id"]: lock for lock in locks}

    coordinator = ZigbangCoordinator(hass, entry.entry_id)
    coordinator.data = {
        tp_id: {
            "locked": lock.get("locked"),
            "battery_raw": lock.get("battery_raw"),
            "rssi": None,
            "last_access": None,
            "last_method": None,
            "last_event_at": None,
            "last_pin_id": None,
        }
        for tp_id, lock in locks_by_tpid.items()
    }

    host, port = _relay_target(entry)

    def on_message(tp_id: str, payload: dict[str, Any]) -> None:
        _handle_relay_message(hass, entry.entry_id, coordinator, tp_id, payload)

    relay = RelayClient(host=host, port=port, tracked_tpids=set(locks_by_tpid), on_message=on_message)
    relay_task = hass.loop.create_task(relay.run(), name=f"{DOMAIN}_relay_{entry.entry_id}")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "relay": relay,
        "relay_task": relay_task,
        "locks": locks_by_tpid,
    }

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["relay"].stop()
        data["relay_task"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await data["relay_task"]
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _relay_target(entry: ConfigEntry) -> tuple[str, int]:
    # options 에서 설정 변경 가능(OptionsFlow) — 없으면 최초 설정값(entry.data) 사용.
    host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
    port = entry.options.get(CONF_PORT, entry.data[CONF_PORT])
    return host, port


def _handle_relay_message(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: ZigbangCoordinator,
    tp_id: str,
    payload: dict[str, Any],
) -> None:
    msg_code = payload.get("msgCode")
    data = payload.get("data")
    if not isinstance(data, dict):
        return

    if msg_code == "Basic-AttrGroup":
        patch = extract_basic_attrgroup_patch(data)
        if patch:
            _merge_state(coordinator, tp_id, patch)
        return

    if msg_code == "IDPEVENT":
        event = extract_idpevent(data, payload.get("msgDate"))
        if event is None:
            return
        patch: dict[str, Any] = {"last_event_at": event.get("at"), "last_pin_id": event.get("pin_id")}
        if "locked" in event:
            patch["locked"] = event["locked"]
            patch["last_access"] = event.get("access")
            patch["last_method"] = ACCESS_LABELS.get(event.get("access"))
        _merge_state(coordinator, tp_id, patch)
        async_dispatcher_send(hass, SIGNAL_EVENT.format(entry_id, tp_id), event)
        return


def _merge_state(coordinator: ZigbangCoordinator, tp_id: str, patch: dict[str, Any]) -> None:
    new_data = dict(coordinator.data)
    new_data[tp_id] = {**new_data.get(tp_id, {}), **patch}
    coordinator.async_set_updated_data(new_data)
