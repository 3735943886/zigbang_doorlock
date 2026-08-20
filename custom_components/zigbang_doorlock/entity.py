"""플랫폼 공용 베이스 엔티티 — device_info + 상태캐시 접근자."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZigbangCoordinator


class ZigbangEntity(CoordinatorEntity[ZigbangCoordinator]):
    def __init__(self, coordinator: ZigbangCoordinator, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._tp_id = tp_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, tp_id)},
            name=lock_info.get("name") or "Zigbang 도어락",
            manufacturer=MANUFACTURER,
            model=lock_info.get("model"),
        )

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._tp_id, {})
