"""락↔relay 연결상태 센서.

relay(0.1.7+)가 observer 로 내보내는 세션 이벤트(relay/session/<tpId>, relay/sessions 스냅샷)로 갱신 —
relay_client.py 의 OnSession 참조. 세션 이벤트가 없는 구버전 relay 에선 락 트래픽이 보이면 "연결"로만
바뀌고, observer 연결이 끊기면 "끊김"이 된다.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(ZigbangRelayConnectedSensor(coordinator, tp_id, lock_info) for tp_id, lock_info in locks.items())


class ZigbangRelayConnectedSensor(ZigbangEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "relay_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _requires_relay = False  # 끊겨있다는 것 자체를 보여줘야 하므로 항상 available

    def __init__(self, coordinator: ZigbangCoordinator, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self._attr_unique_id = f"{tp_id}_relay_connected"

    @property
    def is_on(self) -> bool | None:
        return self._state.get("relay_connected")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        changed_at = self._state.get("relay_changed_at")
        return {
            "peer": self._state.get("relay_peer"),
            "changed_at": changed_at.isoformat() if changed_at else None,
        }
