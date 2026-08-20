"""활동기록 event 엔티티 — IDPEVENT(잠금상태변경/키등록/키삭제)를 실시간으로 이산 이벤트화.

coordinator(연속상태: lock/sensor용)와는 별도로 dispatcher signal 로 "새 이벤트 1건"만 받는다
— coordinator 갱신은 배터리 push 등 이벤트가 아닌 것도 포함해서 매번 트리거하면 중복 발화됨.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_TYPES, SIGNAL_EVENT
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(
        ZigbangActivityEvent(coordinator, entry.entry_id, tp_id, lock_info) for tp_id, lock_info in locks.items()
    )


class ZigbangActivityEvent(ZigbangEntity, EventEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "activity"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: ZigbangCoordinator, entry_id: str, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self._entry_id = entry_id
        self._attr_unique_id = f"{tp_id}_activity"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_EVENT.format(self._entry_id, self._tp_id), self._handle_relay_event
            )
        )

    @callback
    def _handle_relay_event(self, event: dict[str, Any]) -> None:
        self._trigger_event(
            event["event_type"],
            {
                "access": event.get("access"),
                "pin_id": event.get("pin_id"),
                "pin_type": event.get("pin_type"),
                "pin_name": event.get("pin_name"),
            },
        )
        self.async_write_ha_state()
