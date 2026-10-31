"""플랫폼 공용 베이스 엔티티 — device_info + 상태캐시 접근자."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, RELAY_OFFLINE_GRACE
from .coordinator import ZigbangCoordinator


class ZigbangEntity(CoordinatorEntity[ZigbangCoordinator]):
    # 락이 relay 에서 RELAY_OFFLINE_GRACE 초 넘게 끊겨있으면 unavailable — 연결상태 센서 자신만 False.
    _requires_relay = True

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

    @property
    def available(self) -> bool:
        if not super().available or not self._requires_relay:
            return super().available
        state = self._state
        # None(아직 모름: HA 시작 직후, 세션 이벤트 없는 구버전 relay)이면 예전처럼 available.
        if state.get("relay_connected") is not False:
            return True
        changed_at = state.get("relay_changed_at")
        return changed_at is not None and dt_util.utcnow() - changed_at < timedelta(seconds=RELAY_OFFLINE_GRACE)
