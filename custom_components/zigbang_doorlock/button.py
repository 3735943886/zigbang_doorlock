"""pin 레지스트리 수동 새로고침 버튼.

앱에서 pin 이름/카드 등록정보만 바꾸면 도어락 쪽엔 아무 MQTT 트래픽도 안 감(실측 확인) —
relay 관찰로는 그 변경을 원리적으로 절대 못 알아챈다. HA 시작시 1회 자동시딩만으론 부족해서
(그 이후 바뀐 건 재시작 전까지 반영 안 됨), 언제든 눌러서 REST 로 다시 받아오는 버튼을 둔다.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import async_refresh_pin_registry
from .const import DOMAIN
from .coordinator import ZigbangCoordinator
from .entity import ZigbangEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ZigbangCoordinator = data["coordinator"]
    locks: dict[str, dict[str, Any]] = data["locks"]

    async_add_entities(
        ZigbangRefreshPinRegistryButton(coordinator, entry, tp_id, lock_info) for tp_id, lock_info in locks.items()
    )


class ZigbangRefreshPinRegistryButton(ZigbangEntity, ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "refresh_pin_registry"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZigbangCoordinator, entry: ConfigEntry, tp_id: str, lock_info: dict[str, Any]) -> None:
        super().__init__(coordinator, tp_id, lock_info)
        self._entry = entry
        self._attr_unique_id = f"{tp_id}_refresh_pin_registry"

    async def async_press(self) -> None:
        try:
            await async_refresh_pin_registry(self.hass, self._entry, self._tp_id)
        except Exception as err:
            raise HomeAssistantError(f"pin 레지스트리 새로고침 실패: {err}") from err
